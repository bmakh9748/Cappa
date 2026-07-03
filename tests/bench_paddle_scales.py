"""Detection latency vs input scale: how cheap can the det scan get while
still finding the caption?"""

import time

import numpy as np
import cv2
from paddleocr import TextDetection

rng = np.random.default_rng(3)
img = rng.integers(0, 180, (540, 960, 3), dtype=np.uint8)
TEXT = "This is a burned in caption line"
cv2.putText(img, TEXT, (140, 480), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
            (0, 0, 0), 8, cv2.LINE_AA)
cv2.putText(img, TEXT, (140, 480), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
            (255, 255, 255), 2, cv2.LINE_AA)

model = TextDetection(model_name="PP-OCRv5_mobile_det", enable_mkldnn=False)
model.predict(img)  # warm up once at full size

for scale in (1.0, 0.5, 0.4, 0.33, 0.25):
    small = cv2.resize(img, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA)
    times, found = [], 0
    for _ in range(6):
        t0 = time.perf_counter()
        out = model.predict(small)
        times.append(time.perf_counter() - t0)
    res = out[0]
    boxes = []
    for poly in res["dt_polys"]:
        xs = [int(p[0] / scale) for p in poly]
        ys = [int(p[1] / scale) for p in poly]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    print("scale %.2f (%4dx%3d): mean %4.0f ms  min %4.0f ms  boxes=%d %s"
          % (scale, small.shape[1], small.shape[0],
             np.mean(times) * 1e3, np.min(times) * 1e3, len(boxes), boxes))
