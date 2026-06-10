import sys
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from app.services.document_conversion.html_to_pdf import convert_html_to_pdf

@pytest.mark.asyncio
@patch("app.services.document_conversion.html_to_pdf.chrome_executable")
@patch("subprocess.Popen")
@patch("time.time")
@patch("weasyprint.HTML")
async def test_convert_html_to_pdf_linux(mock_weasy_html, mock_time, mock_popen, mock_chrome_exec):
    mock_chrome_exec.return_value = "/usr/bin/google-chrome"
    mock_time.side_effect = [1000.0, 1010.0]  # Fails deadline immediately
    
    # Mock subprocess.Popen
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    
    # Mock weasyprint HTML write_pdf
    mock_weasy_instance = MagicMock()
    mock_weasy_html.return_value = mock_weasy_instance

    src = Path("/tmp/src.html")
    tgt = Path("/tmp/tgt.pdf")
    
    with patch("sys.platform", "linux"):
        res = await convert_html_to_pdf(src, tgt, "tgt.pdf", {})
        
    assert mock_popen.called
    args = mock_popen.call_args[0][0]
    assert "--no-sandbox" in args
    assert "--disable-setuid-sandbox" in args
    assert "WeasyPrint" in res


@pytest.mark.asyncio
@patch("app.services.document_conversion.html_to_pdf.chrome_executable")
@patch("subprocess.Popen")
@patch("time.time")
@patch("weasyprint.HTML")
async def test_convert_html_to_pdf_darwin(mock_weasy_html, mock_time, mock_popen, mock_chrome_exec):
    mock_chrome_exec.return_value = "/usr/bin/google-chrome"
    mock_time.side_effect = [1000.0, 1010.0]  # Fails deadline immediately
    
    # Mock subprocess.Popen
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc
    
    # Mock weasyprint HTML write_pdf
    mock_weasy_instance = MagicMock()
    mock_weasy_html.return_value = mock_weasy_instance

    src = Path("/tmp/src.html")
    tgt = Path("/tmp/tgt.pdf")
    
    with patch("sys.platform", "darwin"):
        res = await convert_html_to_pdf(src, tgt, "tgt.pdf", {})
        
    assert mock_popen.called
    args = mock_popen.call_args[0][0]
    assert "--no-sandbox" not in args
    assert "--disable-setuid-sandbox" not in args
    assert "WeasyPrint" in res
