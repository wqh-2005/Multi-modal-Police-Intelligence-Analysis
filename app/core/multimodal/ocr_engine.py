from typing import Tuple

from paddlex import create_pipeline
import logging

logger = logging.getLogger(__name__)

pipeline = None
try:
    pipeline = create_pipeline(pipeline="OCR")
except Exception as e:
    logger.error(f"Paddlex 模型加载失败: {e}")

async def transmit_image_content(file_path: str) -> Tuple[str, float]:
    """
    使用 PaddleX OCR 识别图片内容
    :param file_path: 图片在服务器上的真实路径
    :return: (识别后的文字内容, 平均置信度)
    """
    if pipeline is None:
        logger.error("OCR引擎未启动")
        return "识别失败", 0.0
    try:
        output = pipeline.predict(file_path)
        all_texts = []
        all_scores = []
        for res in output:
            dt = res.json
            rec_texts = dt["res"].get("rec_texts", [])
            rec_scores = dt["res"].get("rec_scores", [])

            for text in rec_texts:
                if isinstance(text, list):
                    all_texts.append(" ".join(text))
                else:
                    all_texts.append(str(text))
            for score in rec_scores:
                all_scores.append(float(score))

        full_text = "\n".join(all_texts) if all_texts else "未识别到文字"

        avg_confidence = sum(all_scores) / len(all_scores) if len(all_scores) > 0 else 0.0

        return full_text, round(avg_confidence, 2)
    except Exception as e:
        logger.error(f"OCR 识别过程中出错: {e}")
        return f"识别失败", 0.0

if __name__ == "__main__":
    pass