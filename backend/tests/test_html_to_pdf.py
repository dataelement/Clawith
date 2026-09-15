from http.client import BadStatusLine
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.document_conversion.chrome_renderer import collect_browser_layout, stop_process
from app.services.document_conversion.html_to_pdf import convert_html_to_pdf


@pytest.mark.asyncio
async def test_stop_process_kills_and_reaps_after_timeout() -> None:
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock(side_effect=[TimeoutError, 0])

    await stop_process(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.await_count == 2


@pytest.mark.asyncio
@patch("app.services.document_conversion.html_to_pdf.chrome_executable")
@patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("time.time")
@patch("weasyprint.HTML")
async def test_convert_html_to_pdf_linux(
    mock_weasy_html: MagicMock,
    mock_time: MagicMock,
    mock_create_subprocess: AsyncMock,
    mock_chrome_exec: MagicMock,
) -> None:
    mock_chrome_exec.return_value = "/usr/bin/google-chrome"
    mock_time.side_effect = [1000.0, 1010.0]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)
    mock_create_subprocess.return_value = mock_proc

    mock_weasy_instance = MagicMock()
    mock_weasy_html.return_value = mock_weasy_instance

    src = Path("/tmp/src.html")
    tgt = Path("/tmp/tgt.pdf")
    with patch("sys.platform", "linux"):
        res = await convert_html_to_pdf(src, tgt, "tgt.pdf", {})

    assert mock_create_subprocess.called
    args = mock_create_subprocess.call_args.args
    assert "--no-sandbox" in args
    assert "--disable-setuid-sandbox" in args
    mock_proc.terminate.assert_called_once_with()
    mock_proc.wait.assert_awaited_once_with()
    mock_weasy_instance.write_pdf.assert_called_once_with(str(tgt))
    assert "WeasyPrint" in res


@pytest.mark.asyncio
@patch("app.services.document_conversion.html_to_pdf.chrome_executable")
@patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("time.time")
@patch("weasyprint.HTML")
async def test_convert_html_to_pdf_darwin(
    mock_weasy_html: MagicMock,
    mock_time: MagicMock,
    mock_create_subprocess: AsyncMock,
    mock_chrome_exec: MagicMock,
) -> None:
    mock_chrome_exec.return_value = "/usr/bin/google-chrome"
    mock_time.side_effect = [1000.0, 1010.0]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)
    mock_create_subprocess.return_value = mock_proc

    mock_weasy_instance = MagicMock()
    mock_weasy_html.return_value = mock_weasy_instance

    src = Path("/tmp/src.html")
    tgt = Path("/tmp/tgt.pdf")
    with patch("sys.platform", "darwin"):
        res = await convert_html_to_pdf(src, tgt, "tgt.pdf", {})

    assert mock_create_subprocess.called
    args = mock_create_subprocess.call_args.args
    assert "--no-sandbox" not in args
    assert "--disable-setuid-sandbox" not in args
    mock_proc.terminate.assert_called_once_with()
    mock_proc.wait.assert_awaited_once_with()
    mock_weasy_instance.write_pdf.assert_called_once_with(str(tgt))
    assert "WeasyPrint" in res


@pytest.mark.asyncio
@patch("app.services.document_conversion.html_to_pdf.chrome_executable", return_value=None)
@patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("weasyprint.HTML")
async def test_convert_html_to_pdf_without_chrome_uses_weasyprint(
    mock_weasy_html: MagicMock,
    mock_create_subprocess: AsyncMock,
    _mock_chrome_exec: MagicMock,
) -> None:
    src = Path("/tmp/src.html")
    tgt = Path("/tmp/tgt.pdf")

    result = await convert_html_to_pdf(src, tgt, "tgt.pdf", {})

    mock_create_subprocess.assert_not_awaited()
    mock_weasy_html.return_value.write_pdf.assert_called_once_with(str(tgt))
    assert "WeasyPrint" in result


@pytest.mark.asyncio
@patch("app.services.document_conversion.html_to_pdf.read_json_url")
@patch("app.services.document_conversion.html_to_pdf.chrome_executable", return_value="/usr/bin/google-chrome")
@patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("weasyprint.HTML")
async def test_convert_html_to_pdf_bad_devtools_response_uses_weasyprint(
    mock_weasy_html: MagicMock,
    mock_create_subprocess: AsyncMock,
    _mock_chrome_exec: MagicMock,
    mock_read_json_url: MagicMock,
) -> None:
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock(return_value=0)
    mock_create_subprocess.return_value = process
    mock_read_json_url.side_effect = [{}, BadStatusLine("invalid status")]

    result = await convert_html_to_pdf(Path("/tmp/src.html"), Path("/tmp/tgt.pdf"), "tgt.pdf", {})

    mock_weasy_html.return_value.write_pdf.assert_called_once_with("/tmp/tgt.pdf")
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()
    assert "WeasyPrint" in result


@pytest.mark.asyncio
@patch("app.services.document_conversion.chrome_renderer.read_json_url")
@patch("app.services.document_conversion.chrome_renderer.chrome_executable", return_value="/usr/bin/google-chrome")
@patch("asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_collect_browser_layout_bad_devtools_response_uses_dom_fallback(
    mock_create_subprocess: AsyncMock,
    _mock_chrome_exec: MagicMock,
    mock_read_json_url: MagicMock,
) -> None:
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock(return_value=0)
    mock_create_subprocess.return_value = process
    mock_read_json_url.side_effect = [{}, BadStatusLine("invalid status")]

    result = await collect_browser_layout(Path("/tmp/src.html"), 1280, 720, "editable")

    assert result is None
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()
