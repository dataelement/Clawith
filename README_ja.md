# Clawith

現在の `develop` ブランチは Backend clean-break 書き換えの G002 段階です。利用できるのは対象設定、データベースリソースのライフサイクル、`/api/health` のみです。製品 API、Agent Runtime、認証、Frontend、マイグレーション、本番デプロイはまだ利用できません。

ローカルの health-only 検証は [README.md](README.md) を参照してください。`setup.sh` は `backend/.env` と `clawith_target` のみを準備し、`restart.sh` は単一のヘルス worker のみを起動します。Docker、Helm、release、upgrade、Alembic は G002 の製品起動経路ではありません。
