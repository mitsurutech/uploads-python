"""
A file upload, end to end: the browser, S3 and Postgres.

Four routes and nothing else, so the whole thing can be read in one sitting:

  GET  /                  the page: a form, and a table of what has been uploaded
  POST /upload            keep one file
  GET  /files/<id>        send one back
  POST /files/<id>/delete forget one

Everything about *how* the two stores work is in `store.py`. This file is about the web.
"""

import os
import sys
import uuid

from flask import Flask, Response, redirect, request
from werkzeug.exceptions import HTTPException

from page import page
from store import add_file, file_by_id, list_files, read_file, remove_file, set_up


app = Flask(__name__)

# **The file is kept in memory and not on disk**, which is right here and would be wrong
# for a bigger one. Your app's filesystem does not survive a restart, so writing uploads
# there would lose them; and the point of this sample is that S3 is where bytes live. Ten
# megabytes is a limit somebody chose, not a rule: a file larger than this should be
# streamed straight to S3 rather than held whole.
MAX_BYTES = 10 * 1024 * 1024


class FileTooLarge(Exception):
    """Said on the error page, so it reads like a sentence."""


@app.get("/")
def home():
    return page(list_files())


@app.post("/upload")
def upload():
    sent = request.files.get("file")
    if sent is None or not sent.filename:
        return page(list_files(), "Choose a file first."), 400

    # One byte more than the limit is enough to know the file is too big, without reading
    # the whole of something enormous into memory first.
    data = sent.stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise FileTooLarge("File too large")

    # **A uuid for the key, never the name somebody typed.** Two people uploading
    # `notes.txt` must not overwrite each other, and a name that arrived from a browser is
    # not a thing to build a path out of: `../../etc/passwd` is a perfectly good filename
    # as far as a form is concerned. The real name is a column, which is where it is safe.
    add_file(
        key=str(uuid.uuid4()),
        name=sent.filename,
        data=data,
        content_type=sent.mimetype,
    )

    # **See the other page, not this one.** A refresh after a POST re-posts it, which is
    # how somebody uploads the same file three times by pressing F5.
    return redirect("/", code=303)


@app.get("/files/<file_id>")
def download(file_id):
    file = file_by_id(file_id)
    if not file:
        return Response("There is no such file.", status=404, mimetype="text/plain")

    body = read_file(file["s3_key"])

    def chunks():
        try:
            yield from body.iter_chunks(64 * 1024)
        finally:
            body.close()

    # **Always an attachment, and always `octet-stream`.** Anybody can upload anything
    # here, and an HTML file served inline would run as a page on this app's own address.
    # Saved to disk it is inert. `nosniff` stops a browser guessing a type of its own and
    # undoing the first two.
    safe_name = file["name"].replace('"', "").replace("\\", "")
    return Response(chunks(), headers={
        "Content-Type": "application/octet-stream",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="{safe_name}"',
    })


@app.post("/files/<file_id>/delete")
def delete(file_id):
    remove_file(file_id)
    return redirect("/", code=303)


@app.errorhandler(Exception)
def something_went_wrong(why):
    """Anything unhandled, said rather than swallowed: a blank page teaches nobody anything."""
    if isinstance(why, HTTPException):
        # A wrong address or method is Flask's own answer to give.
        return why
    app.logger.error("unhandled error", exc_info=why)
    return page([], f"Something went wrong: {why}"), 500


# **Set-up runs when this file is loaded**, because gunicorn loads it and never runs the
# `__main__` block below. **Stop rather than serve a broken app.** An app that starts
# without its bucket or its table answers every request with the same error, and the logs
# are the only place that says why; failing at the start puts the reason where somebody is
# already looking. The error is raised again rather than ending the process with
# `sys.exit`: gunicorn then reports "Worker failed to boot" and stops, where a worker that
# merely exits would be started again for ever.
try:
    set_up()
except Exception as why:
    print(f"Could not set up the stores: {why!r}", file=sys.stderr, flush=True)
    raise


if __name__ == "__main__":
    # **`PORT` comes from the platform and must not be hard-coded.** Your app is one
    # service on a private network and the door in front of it dials the port it was told
    # about. On the platform gunicorn is given it on the command line; this block is only
    # for running `python app.py` on your own machine.
    port = int(os.environ.get("PORT") or 8080)
    app.run(host="0.0.0.0", port=port)
