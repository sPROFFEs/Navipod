import json
import logging
import os
from typing import Dict
logger = logging.getLogger(__name__)

# Global dictionary to hold loaded translations including nested keys
# Structure: { "es": { "key": "value" }, "en": { ... } }
translations: Dict[str, Dict[str, str]] = {}

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ["en"]

def load_translations(locales_dir: str = "locales"):
    """Loads all JSON files from the locales directory."""
    global translations
    if not os.path.exists(locales_dir):
        logger.warning("Locales directory not found: %s", locales_dir)
        return

    for filename in os.listdir(locales_dir):
        if filename.endswith(".json"):
            lang_code = filename.split(".")[0]
            if lang_code not in SUPPORTED_LANGS:
                continue
            try:
                with open(os.path.join(locales_dir, filename), "r", encoding="utf-8") as f:
                    translations[lang_code] = json.load(f)
                logger.info("Loaded %s translations", lang_code)
            except Exception as e:
                logger.warning("Error loading locale file %s: %s", filename, e)

def get_text(key: str, lang: str = DEFAULT_LANG) -> str:
    """
    Retrieves the translation for a given key in the specified language.
    Falls back to DEFAULT_LANG if key is missing/lang not found.
    Returns the key itself if not found anywhere.
    """
    # 1. Try requested language
    # if key == "login.title": print(f"[I18N-DEBUG] Looking up '{key}' in '{lang}'. Loaded: {list(translations.keys())}")
    if lang in translations and key in translations[lang]:
        return translations[lang][key]
    
    # 2. Try default language fallback
    if lang != DEFAULT_LANG and DEFAULT_LANG in translations and key in translations[DEFAULT_LANG]:
        return translations[DEFAULT_LANG][key]

    # 3. Return key as last resort
    return key
