import pytest

from app.services.timezone_utils import validate_timezone_name


@pytest.mark.parametrize("timezone_name", ["UTC", "Asia/Shanghai", "America/New_York"])
def test_validate_timezone_name_accepts_valid_iana_name(timezone_name: str) -> None:
    assert validate_timezone_name(timezone_name) == timezone_name


@pytest.mark.parametrize("timezone_name", ["", "Not/A_Timezone"])
def test_validate_timezone_name_rejects_invalid_name(timezone_name: str) -> None:
    with pytest.raises(ValueError, match=f"^Invalid IANA timezone: {timezone_name}$"):
        validate_timezone_name(timezone_name)


def test_validate_timezone_name_preserves_non_string_type_error() -> None:
    with pytest.raises(TypeError):
        validate_timezone_name(123)  # type: ignore[arg-type]
