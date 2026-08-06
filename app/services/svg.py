import os
import uuid

import cairosvg
from lxml import etree

from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger

from app.config.settings import settings


class SvgService:
    def __init__(self) -> None:
        self.PROFIT_TEMPLATE_PATH = settings.profit_template_path
        self.ENTRY_TEMPLATE_PATH = settings.entry_template_path
        self.OUTPUT_DIR = settings.svg_output_dir
        self.PROFIT_COLOR = "#0DB459"
        self.LOSS_COLOR = "#E63F25"
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)

    def _tehran_now_str(self) -> str:
        return datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")

    def clear_shot_file(self, path: str) -> bool:
        try:
            os.remove(path=path)
            return True
        except Exception as e:
            logger.exception('something happen during removing file: {}\nexception: {}', path, e)
            return False

    def generate_profit_shot(
        self, pair: str, direction: str, leverage: str|int,
        pnl: str, entry: str, exit_price: str,
        datetime_str: str|None=None
        ) -> str:
        """
        Edits the TpHit template with the given values and renders it to a
        PNG using cairosvg. Returns the path to the generated PNG (randomly
        named).

        `pnl` can be a profit (e.g. "107.08") or a loss (e.g. "-114.48").
        If it's negative, the pnl text is switched to red instead of green.
        """
        if not datetime_str:
            datetime_str = self._tehran_now_str()

        pair = pair.upper().replace('/', '').replace('USDT', '')

        is_loss = float(pnl) < 0
        pnl_text = f"{pnl}%" if pnl.startswith("-") else f"+{pnl}%"

        tree = etree.parse(self.PROFIT_TEMPLATE_PATH)
        root = tree.getroot()

        values = {
            "pair": f"{pair} USDT",
            "directionAndLeverage": f"Perpetual | {direction} | {leverage}X",
            "profit": pnl_text,
            "entry": entry,
            "exit": exit_price,
            "datetime": datetime_str,
        }

        for elem_id, text in values.items():
            elem = root.find(f".//*[@id='{elem_id}']")
            if elem is None:
                print(f"[WARN] no element with id={elem_id!r}")
                continue
            elem.text = text

        if is_loss:
            profit_elem = root.find(".//*[@id='profit']")
            if profit_elem is not None:
                profit_elem.set("style", f"fill:{self.LOSS_COLOR};")

        svg_bytes = etree.tostring(tree)

        png_path = os.path.join(self.OUTPUT_DIR, f"{uuid.uuid4().hex}.png")
        cairosvg.svg2png(bytestring=svg_bytes, write_to=png_path, url=self.PROFIT_TEMPLATE_PATH, scale=2)
        logger.info('profit shot generated, path: {}', png_path)
        return png_path

    def generate_entry_shot(
        self, pair: str, direction: str, leverage: str|int,
        entry: str, datetime_str: str|None=None
        ) -> str:
        """
        Edits the EntryHit template with the given values and renders it to a
        PNG using cairosvg. Returns the path to the generated PNG (randomly
        named).
        """
        if not datetime_str:
            datetime_str = self._tehran_now_str()

        pnl_text = "+0%"

        pair = pair.upper().replace('/', '').replace('USDT', '')

        tree = etree.parse(self.PROFIT_TEMPLATE_PATH)
        root = tree.getroot()

        values = {
            "pair": f"{pair} USDT",
            "directionAndLeverage": f"Perpetual | {direction} | {leverage}X",
            "profit": pnl_text,
            "entry": entry,
            "exit": entry,
            "datetime": datetime_str,
        }

        for elem_id, text in values.items():
            elem = root.find(f".//*[@id='{elem_id}']")
            if elem is None:
                print(f"[WARN] no element with id={elem_id!r}")
                continue
            elem.text = text

        svg_bytes = etree.tostring(tree)

        png_path = os.path.join(self.OUTPUT_DIR, f"{uuid.uuid4().hex}.png")
        cairosvg.svg2png(bytestring=svg_bytes, write_to=png_path, url=self.PROFIT_TEMPLATE_PATH, scale=2)
        logger.info('entry shot generated, path: {}', png_path)
        return png_path
