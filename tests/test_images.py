import io

from PIL import Image

from barrierfrei import images


def jpeg(width=64, height=48) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="JPEG")
    return out.getvalue()


def test_media_type_detection():
    assert images.media_type(b"\x89PNG....") == "image/png"
    assert images.media_type(b"RIFF1234WEBPxx") == "image/webp"
    assert images.media_type(jpeg()) == "image/jpeg"


def test_draw_boxes_keeps_size_and_marks_pixels():
    out = images.draw_boxes(jpeg(), [("curb", (10, 10, 30, 30))])
    image = Image.open(io.BytesIO(out)).convert("RGB")
    assert image.size == (64, 48)
    red, green, _ = image.getpixel((10, 20))
    assert red > 200 and green < 80


class FakeSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, timeout):
        self.calls += 1
        return type("R", (), {"content": jpeg(), "raise_for_status": lambda self: None})()


def test_load_photo_downloads_once(tmp_path):
    session = FakeSession()
    images.load_photo(session, "123", "https://a?sig=1", tmp_path)
    images.load_photo(session, "123", "https://a?sig=2", tmp_path)  # re-signed URL, same photo
    assert session.calls == 1
