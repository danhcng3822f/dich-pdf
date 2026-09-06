from app.config import settings


def test_settings_directories():
    assert settings.upload_dir.exists()
    assert settings.export_dir.exists()
    assert settings.fonts_dir.exists()
    assert settings.models_dir.exists()
    assert settings.previews_dir.exists()
