"""Константы приложения MAX POST."""

# UI
ANIM_FRAME_INTERVAL_MS = 14    # ~70 fps для анимации галочки
ANIM_STEP = 0.030              # шаг анимации 0→1 за ~33 кадра ≈ 470 мс
ANIM_DURATION_MS = 2000        # длительность показа оверлея успеха

PARSE_DEBOUNCE_MS = 450        # дебаунс автопарсинга адресов
SAVE_DEBOUNCE_MS = 400         # дебаунс сохранения состояния
UPDATE_CHECK_DELAY_MS = 2000   # задержка проверки обновлений после запуска

TEXT_CHAR_LIMIT = 4000         # лимит символов в сообщении MAX

# Размеры
EMOJI_PICKER_WIDTH = 310
EMOJI_PICKER_HEIGHT = 300
EMOJI_GRID_COLS = 8
EMOJI_BTN_SIZE = 32
ANIM_WIDGET_SIZE = 148

# Цвета (QSS)
COLOR_BG = "#f3f4f6"
COLOR_WHITE = "#ffffff"
COLOR_BORDER = "#e4eaf0"
COLOR_PRIMARY = "#2d6cdf"
COLOR_SUCCESS = "#22c55e"
COLOR_SUCCESS_DARK = "#16a34a"
COLOR_TEXT_MUTED = "#aab0bb"
COLOR_NUM_LABEL = "#c4cdd8"

# История
HISTORY_MAX_ENTRIES = 200
HISTORY_SNIPPET_LEN = 60

# VK API
VK_API_URL         = "https://api.vk.com/method"
VK_API_VERSION     = "5.199"
VK_MAX_PHOTO_MB    = 50          # лимит размера фото для загрузки на стену
VK_MAX_ATTACHMENTS = 10          # максимум вложений в одном сообщении
VK_RETRY_DELAYS    = (1, 2, 4)   # паузы между попытками при сетевой ошибке
