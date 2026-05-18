import base64
import io
import subprocess
import zipfile
from pathlib import Path


def test_build_skill_folder_zip_marks_filenames_as_utf8():
    repo_root = Path(__file__).resolve().parents[2]
    frontend_dir = repo_root / "frontend"
    script = """
import { buildSkillFolderZip } from './src/utils/skillFolderZip.ts';

const file = new File(['# demo\\n'], '技能.md', { type: 'text/markdown' });
const zipFile = await buildSkillFolderZip([{ path: '技能.md', file }], 'demo-skill');
const bytes = new Uint8Array(await zipFile.arrayBuffer());
process.stdout.write(Buffer.from(bytes).toString('base64'));
""".strip()

    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module"],
        cwd=frontend_dir,
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )

    zip_bytes = base64.b64decode(completed.stdout.strip())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert archive.namelist() == ["demo-skill/技能.md"]
