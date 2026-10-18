# Téléversement de fichiers en Python : S3 pour les octets, Postgres pour tout le reste

[English](README.md) | [Français](README.fr.md) | [日本語](README.ja.md) | [Español](README.es.md)

Une application d’exemple pour la [plateforme d’apprentissage MitsuruTech](https://mitsurutech.co.uk/fr), écrite avec
Flask. Vous téléversez un fichier ; S3 le conserve ; Postgres retient où il est allé et comment il
s’appelle. Le tableau de la page vient de Postgres, et chaque téléchargement vient de S3.

Elle est volontairement petite, quatre routes et trois fichiers, pour que vous puissiez lire
l’ensemble avant de modifier quoi que ce soit.

```
app.py            le web : quatre routes et rien d'autre
store.py          les deux stockages, et l'ordre dans lequel on y écrit
page.py           le HTML
requirements.txt  Flask, boto3 et psycopg2
```

## La déployer ici

Ouvrez **Deploy** sur la plateforme, choisissez ce dépôt, et démarrez une **base de données**
et la **pile cloud** à côté. Tout ce dont l’application a besoin se trouve alors déjà dans
son environnement ; il n’y a rien à configurer. La plateforme installe `requirements.txt`
et lance l’application avec `gunicorn app:app`.

| Variable | Définie par | Ce que c’est |
| --- | --- | --- |
| `DATABASE_URL` | votre session | le serveur Postgres qui tourne sous le nom `db` |
| `AWS_ENDPOINT_URL` | votre session | la pile cloud qui tourne sous le nom `cloud` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | votre session | les identifiants pour y accéder |
| `PORT` | la plateforme | le port sur lequel écouter |
| `BUCKET_NAME` | vous, si vous voulez | `uploads` par défaut |

## Les trois choses à lire

**1. `Config(s3={"addressing_style": "path"})`, dans `store.py`.** C’est le réglage que tout
le monde oublie en premier. Par défaut, boto3 place le bucket dans le nom d’hôte, `uploads.cloud`, et
ce nom n’existe pas sur le réseau de votre session : chaque appel échoue donc avec une
erreur DNS qui ne dit rien des buckets. Le style « path » demande plutôt
`http://cloud:4566/uploads/...`.

**2. L’application crée son propre bucket.** Votre pile cloud démarre complètement vide,
exactement comme un vrai compte AWS que vous venez d’ouvrir. `create_bucket` est le travail
de votre code, il s’exécute à chaque démarrage, et il intercepte « vous êtes déjà propriétaire
de celui-ci » pour qu’un redémarrage ne soit pas un plantage. La table est créée de la même
manière, avec `IF NOT EXISTS`.

**3. Écrire d’abord dans S3, puis la ligne. Supprimer d’abord la ligne, puis S3.**

- Envoyez les octets, puis insérez la ligne. Si l’insertion échoue, il reste un objet vers
  lequel rien ne pointe : invisible, inoffensif, facile à nettoyer.
- Écrivez la ligne d’abord et un envoi raté laisse sur la page un fichier impossible à
  ouvrir, sans moyen de distinguer cette ligne d’une bonne.
- La suppression va dans l’autre sens, pour la même raison.

L’un de ces échecs, ce sont quelques octets perdus. L’autre, c’est une page cassée. Laissez
toujours passer en premier ce qui peut être orphelin sans dégât.

## Deux habitudes de sécurité, prises au sérieux dans un exemple, exprès

**La clé S3 est un uuid, jamais le nom du fichier.** Deux personnes téléversent `notes.txt`
et l’une ne doit pas écraser l’autre ; et un nom venu d’un navigateur n’est pas une base
pour construire un chemin (`../../etc/passwd` est un nom de fichier parfaitement valide pour
un formulaire). Le vrai nom est une colonne, et c’est là qu’il est sans danger.

**Les téléchargements sont toujours `attachment`, toujours `application/octet-stream`,
toujours `nosniff`.** N’importe qui peut téléverser n’importe quoi. Un fichier HTML servi en
ligne s’exécuterait comme une page à l’adresse même de votre application, avec tout ce que
cette page peut atteindre ; enregistré sur le disque, il est inerte.

Et chaque nom de fichier est échappé quand il revient sur la page : un fichier nommé
`<img src=x onerror=alert(1)>` est un nom de fichier tout à fait valable et ne doit pas
devenir du balisage.

## L’exécuter sur votre propre machine

Il vous faut un Postgres et quelque chose qui ressemble à S3. Avec Docker :

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=dev --name pg postgres:16
docker run -d -p 4566:4566 --name cloud localstack/localstack

export DATABASE_URL=postgres://postgres:dev@localhost:5432/postgres
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt gunicorn
gunicorn --bind 0.0.0.0:8080 app:app
```

Ou, pour un lancement rapide sans gunicorn, `python app.py`.

Ouvrez ensuite <http://localhost:8080>.

## À essayer ensuite

- Envoyer un gros fichier directement vers S3 au lieu de le garder en mémoire (`app.py`
  indique où).
- Ajouter un compteur de téléchargements, dans DynamoDB ; votre pile cloud l’émule aussi.
- Mettre les fichiers derrière une connexion, pour que le tableau soit propre à chaque personne.
- Afficher un aperçu d’image, avec une URL pré-signée plutôt qu’en faisant transiter les octets.

## Pour aller plus loin

- [Déployer une application depuis GitHub](https://mitsurutech.co.uk/fr/help/deploy-app) :
  comment la plateforme exécute un dépôt comme celui-ci.
- [La pile cloud](https://mitsurutech.co.uk/fr/help/cloud-stack) : le service de type S3
  que votre session démarre pour que l’application l’utilise.
- Vous enseignez à une classe ? [La première année est gratuite](https://mitsurutech.co.uk/fr/pricing)
  jusqu’à 50 élèves, et [l’inscription](https://mitsurutech.co.uk/fr/signup) ne demande
  qu’une adresse e-mail.

## À propos

Réalisé par [Mitsuru Technologies Ltd](https://mitsurutech.co.uk/fr/learning) (société immatriculée en Angleterre et au pays de Galles, numéro 16755264), qui conçoit la
[plateforme d’apprentissage MitsuruTech](https://mitsurutech.co.uk/fr/learning) pour les écoles et les établissements.

Le même exemple dans d’autres langages : [uploads-node](https://github.com/mitsurutech/uploads-node),
[uploads-go](https://github.com/mitsurutech/uploads-go) et
[uploads-java](https://github.com/mitsurutech/uploads-java).

## Licence

Cette œuvre est sous licence [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Vous pouvez la partager et l’adapter pour apprendre et enseigner, à condition de citer Mitsuru Technologies Ltd, de ne pas l’utiliser commercialement et de partager tout ce que vous en tirez sous la même licence. Le texte complet est dans le fichier `LICENSE`.
