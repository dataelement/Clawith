# Clawith

현재 `develop` 브랜치는 Backend clean-break 재작성의 G002 단계입니다. 지금 제공되는 것은 대상 설정, 데이터베이스 리소스 수명주기, `/api/health`뿐입니다. 제품 API, Agent Runtime, 인증, Frontend, 마이그레이션 및 운영 배포는 아직 사용할 수 없습니다.

로컬 health-only 검증은 [README.md](README.md)를 따르세요. `setup.sh`는 `backend/.env`와 `clawith_target`만 준비하고, `restart.sh`는 단일 health worker만 시작합니다. Docker, Helm, release, upgrade, Alembic은 G002 제품 시작 경로가 아닙니다.
