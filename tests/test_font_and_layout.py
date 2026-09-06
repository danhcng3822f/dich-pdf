from pathlib import Path
import numpy as np
from app.services.pdf2zh_engine.font_manager import get_font_path, get_font_name_for_lang
from app.services.pdf2zh_engine.doclayout import load_layout_model


def test_get_font_path():
    path = get_font_path("vi")
    assert path is not None
    assert Path(path).exists()
    assert str(path).endswith((".ttf", ".otf"))


def test_font_name_mapping():
    assert get_font_name_for_lang("vi") == "GoNotoKurrent-Regular.ttf"
    assert get_font_name_for_lang("vietnamese") == "GoNotoKurrent-Regular.ttf"
    assert get_font_name_for_lang("en") == "GoNotoKurrent-Regular.ttf"
    assert get_font_name_for_lang("zh-cn") == "SourceHanSerifCN-Regular.ttf"
    assert get_font_name_for_lang("zh") == "SourceHanSerifCN-Regular.ttf"
    assert get_font_name_for_lang("ja") == "SourceHanSerifJP-Regular.ttf"
    assert get_font_name_for_lang("ko") == "SourceHanSerifKR-Regular.ttf"
    assert get_font_name_for_lang("zh-tw") == "SourceHanSerifTW-Regular.ttf"


def test_load_layout_model():
    model = load_layout_model()
    assert model is not None
    assert hasattr(model, "predict")
    # Test with dummy image
    dummy_img = np.zeros((300, 300, 3), dtype=np.uint8)
    result = model.predict(dummy_img, imgsz=320)
    assert len(result) > 0
    assert hasattr(result[0], "boxes")
    assert hasattr(result[0], "names")


def test_load_layout_model_singleton():
    model1 = load_layout_model()
    model2 = load_layout_model()
    assert model1 is model2
