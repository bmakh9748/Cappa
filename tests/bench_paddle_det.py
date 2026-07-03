"""How fast is PaddleOCR's pretrained text DETECTOR on this machine, and does
it find caption-style text over a noisy background?"""

import time

import numpy as np
import cv2
from paddleocr import TextDetection

rng = np.random.default_rng(3)
img = rng.integers(0, 180, (540, 960, 3), dtype=np.uint8)  # noisy 'video'
TEXT = "This is a burned in caption line"
cv2.putText(img, TEXT, (140, 480), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
            (0, 0, 0), 8, cv2.LINE_AA)     # outline
cv2.putText(img, TEXT, (140, 480), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
            (255, 255, 255), 2, cv2.LINE_AA)  # fill
cv2.putText(img, "1080p60", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
            (230, 230, 230), 1, cv2.LINE_AA)  # small UI text

model = TextDetection(model_name="PP-OCRv5_mobile_det", enable_mkldnn=False)

t0 = time.perf_counter()
out = model.predict(img)
print("first call (incl. warmup): %.0f ms" % ((time.perf_counter() - t0) * 1e3))

times = []
for _ in range(10):
    t0 = time.perf_counter()
    out = model.predict(img)
    times.append(time.perf_counter() - t0)
print("warm: mean %.0f ms  min %.0f ms"
      % (np.mean(times) * 1e3, np.min(times) * 1e3))

res = out[0]
print("boxes found: %d" % len(res["dt_polys"]))
for poly, score in zip(res["dt_polys"], res["dt_scores"]):
    xs = [int(p[0]) for p in poly]
    ys = [int(p[1]) for p in poly]
    print("  (%4d,%4d)-(%4d,%4d)  score %.2f"
          % (min(xs), min(ys), max(xs), max(ys), score))
