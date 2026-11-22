COSE — Logs Monitoring Dashboard

Le dashboard lit les vrais documents Elasticsearch dans microservices-logs-*.
Il ne modifie et ne supprime aucune donnée.

INSTALLATION DOCKER (recommandée)
1. Copie le dossier cose-logs-dashboard dans C:\Users\ssidi\Documents\TP3COSE\
2. Ouvre ton docker-compose.yml existant.
3. Ajoute le service indiqué dans docker-compose-snippet.yml sous "services:".
   Ne remplace pas ton fichier compose entier. Le réseau microServices doit exister.
4. Depuis le dossier TP3COSE, exécute :
   docker compose up -d --build logs-dashboard
5. Vérifie :
   docker ps --filter "name=logsDashboard"
6. Ouvre http://localhost:8082

INSTALLATION LOCALE
Dans PowerShell, depuis ce dossier :
  py -m pip install -r requirements.txt
  $env:ELASTICSEARCH_URL="http://localhost:9200"
  py app.py
Puis ouvre http://localhost:5000

Filtres : service, level, correlation_id, texte.
API : /api/logs, /api/stats, /health.
Le bandeau statistiques compte les 1000 documents les plus récents;
le tableau affiche au maximum 100 logs.
