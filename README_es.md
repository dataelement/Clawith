# Clawith

La rama `develop` está en G002 de la reescritura clean-break del Backend. Solo están disponibles la configuración objetivo, el ciclo de vida de recursos de base de datos y `/api/health`. Las API de producto, Agent Runtime, autenticación, Frontend, migraciones y despliegue de producción todavía no están disponibles.

Consulta [README.md](README.md) para la validación local health-only. `setup.sh` prepara únicamente `backend/.env` y la base `clawith_target`; `restart.sh` inicia un solo worker de salud. Docker, Helm, releases, upgrades y Alembic no son rutas de inicio de producto en G002.
