"""The real-world case my sim missed: a full 1920x1080 browser capture.
Which working size still finds normal-size captions, and at what cost?"""

import time

import numpy as np
import cv2
from paddleocr import TextDetection

rng = np.random.default_rng(5)
img = rng.integers(0, 170, (1080, 1920, 3), dtype=np.uint8)
# typical 1080p caption (~38px glyphs) and a smaller one (~26px)
for text, y, px in (("This is a normal size caption line", 980, 1.3),
                    ("a smaller second style of caption", 60, 0.9)):
    cv2.putText(img, text, (500, y), cv2.FONT_HERSHEY_SIMPLEX, px,
                (0, 0, 0), 8, cv2.LINE_AA)
    cv2.putText(img, text, (500, y), cv2.FONT_HERSHEY_SIMPLEX, px,
                (255, 255, 255), 2, cv2.LINE_AA)

model = TextDetection(model_name="PP-OCRv5_mobile_det", enable_mkldnn=False)
model.predict(img[:540, :960])  # warm

for side in (480, 640, 736, 896, 1088):
    scale = min(1.0, side / 1920)
    small = cv2.resize(img, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA)
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        out = model.predict(small)
        times.append(time.perf_counter() - t0)
    n = len(out[0]["dt_polys"])
    print("side %4d (%4dx%4d): mean %4.0f ms   boxes=%d/2"
          % (side, small.shape[1], small.shape[0],
             np.mean(times) * 1e3, n))
