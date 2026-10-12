"""
The two stores this app uses, and the one rule about the order they are written in.

S3 holds the bytes and Postgres holds everything else: who the file is called, how big it
is, when it arrived and which S3 key it is under. Neither knows about the other; this
file is the only place that knows about both.

**Write S3 first, then the row. Delete the row first, then S3.** That order is the whole
of the reliability story here and it is worth understanding before you change anything:

- If the upload to S3 works and the insert then fails, you have an object nobody points
  at. Nothing is broken; it is a few bytes nobody can see, and a tidy-up job could sweep
  it later.
- If you wrote the row first and the upload then failed, the page would list a file that
  cannot be opened. Somebody clicks it and gets an error, and there is no way to tell
  that row from a good one.

The first is invisible, the second is a broken page. So always write the thing that can
be orphaned harmlessly first, and delete in the opposite order for the same reason.
"""

import os
from contextlib import contextmanager

import boto3
import psycopg2
import psycopg2.errors
import psycopg2.extras
from botocore.config import Config
from botocore.exceptions import ClientError


# The bucket this app keeps its uploads in.
#
# **The app makes its own bucket and is not given one.** The cloud stack in your session
# starts completely empty, exactly as a real AWS account you just opened would, so
# `create_bucket` is your code's job, and it is one of the things worth learning. A name
# must be lower case and DNS-safe.
BUCKET = os.environ.get("BUCKET_NAME") or "uploads"

REGION = os.environ.get("AWS_REGION") or "us-east-1"

# The S3 client.
#
# **`addressing_style: "path"` is the one setting you must not miss**, and leaving it out
# is the mistake almost everybody makes first. By default boto3 addresses a bucket as part
# of the hostname, `uploads.cloud`, and inside your session there is no such name, so every
# call fails with a DNS error that says nothing about buckets. Path style asks for
# `http://cloud:4566/uploads/...` instead, which is a name that exists.
#
# Everything else comes from the environment your session already set:
# `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_REGION`. boto3
# reads the credentials by itself, which is why they are not mentioned below.
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
    region_name=REGION,
    config=Config(s3={"addressing_style": "path"}),
)


@contextmanager
def db():
    """
    One connection for one piece of work, always given back.

    **A connection per request, closed in a `finally`.** The database your session started
    is at the name `db`, and `DATABASE_URL` is set for you. Opening a connection for every
    request is a little slower than a pool, and a sample this size does not notice; what it
    buys is that nothing can be left open when something goes wrong half way. Commit when
    the work finished, roll back when it did not, close either way.
    """
    connection = psycopg2.connect(
        os.environ.get("DATABASE_URL"), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS files (
      id          bigserial PRIMARY KEY,
      -- **The key, which is where the bytes are.** A uuid rather than the name somebody
      -- uploaded: two people may upload "notes.txt", and a key built from a name is a
      -- file that quietly replaces another one.
      s3_key      text NOT NULL,
      -- What to call it on the page and in the download. Kept apart from the key for the
      -- reason above.
      name        text NOT NULL,
      size_bytes  bigint NOT NULL,
      uploaded_at timestamptz NOT NULL DEFAULT now()
    )
"""


def set_up():
    """
    Make the bucket and the table, if they are not there already.

    **Run on every start and safe to run again**, which is what `IF NOT EXISTS` and catching
    "you already own this bucket" are for. An app that only works the first time it starts
    is an app that breaks the moment it restarts.
    """
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=BUCKET)
        else:
            # Outside us-east-1 real S3 insists on being told where the bucket lives.
            s3.create_bucket(
                Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        print(f"made the bucket {BUCKET}", flush=True)
    except ClientError as why:
        # Already there, and made by us. Any other failure is real and should stop the app,
        # because an app that cannot reach its store has nothing useful to do.
        if why.response.get("Error", {}).get("Code") not in (
                "BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise

    try:
        _create_table()
    except (psycopg2.errors.UniqueViolation, psycopg2.errors.DuplicateTable,
            psycopg2.errors.DuplicateObject):
        # **Several workers start at once**, and Postgres can refuse two `CREATE TABLE IF NOT
        # EXISTS` that run at the same moment even though neither is wrong. The other one
        # won; run it again, which is now a plain no-op, to be sure the table is there.
        _create_table()
    print("the files table is ready", flush=True)


def _create_table():
    with db() as connection, connection.cursor() as cursor:
        cursor.execute(CREATE_TABLE)


def list_files():
    """Every file, newest first."""
    with db() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name, size_bytes, uploaded_at FROM files "
            "ORDER BY uploaded_at DESC, id DESC")
        return cursor.fetchall()


def add_file(key, name, data, content_type):
    """
    Keep one file: the bytes in S3, then the row in Postgres.

    See this file's own note on why that order and not the other.
    """
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type or "application/octet-stream",
    )

    with db() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO files (s3_key, name, size_bytes) VALUES (%s, %s, %s)",
            (key, name, len(data)))


def file_by_id(file_id):
    """One file's row, or None."""
    with db() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, s3_key, name, size_bytes FROM files WHERE id = %s", (file_id,))
        return cursor.fetchone()


def read_file(key):
    """The bytes back out of S3, as a stream."""
    got = s3.get_object(Bucket=BUCKET, Key=key)
    return got["Body"]


def remove_file(file_id):
    """
    Forget one file: the row first, then the bytes.

    **The opposite order from writing, and for the same reason.** Delete the bytes first and
    a failure between the two leaves a row pointing at nothing, which is a broken download.
    Delete the row first and the worst case is an object nobody points at.
    """
    file = file_by_id(file_id)
    if not file:
        return False

    with db() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM files WHERE id = %s", (file_id,))
    s3.delete_object(Bucket=BUCKET, Key=file["s3_key"])
    return True
