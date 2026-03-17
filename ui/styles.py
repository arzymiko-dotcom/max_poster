"""Таблица стилей QSS для MAX POST."""


def get_dark_stylesheet() -> str:
    """Возвращает QSS тёмной темы приложения."""
    return """
            #maxPosterContent { background: #1e1e2e; }
            QGroupBox {
                font-size: 15px;
                font-weight: 600;
                border: 1px solid #3a3a55;
                border-radius: 10px;
                margin-top: 12px;
                background: #252535;
                color: #c8c8e8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 6px;
                color: #a0a0cc;
            }
            #sidePanel {
                border: 1px solid #3a3a55;
                border-radius: 10px;
                background: #252535;
                margin-top: 0;
            }
            QPlainTextEdit, QTextEdit, QLineEdit, QComboBox {
                border: 1px solid #3a3a55;
                border-radius: 8px;
                padding: 8px;
                background: #2a2a3e;
                color: #d8d8f0;
                font-size: 14px;
            }
            QPushButton {
                min-height: 42px;
                border-radius: 8px;
                border: 1px solid #3a3a55;
                background: #2d2d45;
                padding: 6px 12px;
                font-size: 14px;
                color: #c8c8e0;
            }
            QPushButton:hover { background: #353550; }
            QPushButton#primaryButton {
                background: #4a6cf7;
                color: white;
                border: none;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover { background: #5a7cf7; }
            #previewCard {
                border: 1px solid #3a3a55;
                border-radius: 10px;
                background: #252535;
            }
            #postCard { background: #252535; }
            #postText {
                border: none;
                background: #252535;
                font-size: 14px;
                color: #d8d8f0;
                padding: 10px 2px;
            }
            #textContainer {
                border: 1px solid #3a3a55;
                border-radius: 8px;
                background: #2a2a3e;
            }
            #textContainer QPlainTextEdit {
                border: none;
                border-radius: 8px 8px 0 0;
                padding: 8px;
                background: #2a2a3e;
                font-size: 14px;
                color: #d8d8f0;
            }
            #emojiButton {
                min-height: 24px;
                border: none;
                background: transparent;
                font-size: 16px;
                padding: 0;
            }
            #emojiButton:hover { background: #353550; border-radius: 4px; }
            #charCounter { font-size: 12px; color: #6868aa; }
            #emojiPicker {
                border: 1px solid #3a3a55;
                border-radius: 10px;
                background: #252535;
            }
            #emojiBtn {
                min-height: 0;
                border: none;
                background: transparent;
                font-size: 18px;
                padding: 0;
            }
            #emojiBtn:hover { background: #353550; border-radius: 4px; }
            #versionLabel { font-size: 11px; color: #5a5a88; padding: 2px 0; }
            QPushButton#photoButtonDone {
                background: #1a3a2e;
                color: #4ade80;
                border: 1px solid #2a5a3e;
                font-size: 13px;
            }
            #checklistFrame {
                border: 1px solid #3a3a55;
                border-radius: 10px;
                background: #222232;
            }
            #checklistTitle {
                font-size: 12px;
                font-weight: 600;
                color: #7878aa;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            #previewTitle {
                font-size: 13px;
                font-weight: 600;
                color: #7878aa;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            #checklistItem { font-size: 13px; color: #b0b0cc; padding: 1px 0; }
            #historyFrame {
                border: 1px solid #3a3a55;
                border-radius: 10px;
                background: #222232;
            }
            #histEntry {
                background: #2a2a3e;
                border: 1px solid #3a3a55;
                border-radius: 7px;
                padding: 5px 8px;
            }
            #histEmpty { font-size: 12px; color: #5a5a88; padding: 4px 0; }
            #histDate { font-size: 11px; color: #5a5a88; }
            #histText { font-size: 12px; color: #b0b0cc; }
            #histPlatformFallback { font-size: 11px; color: #9090bb; font-weight: 600; }
            QPushButton#histClearBtn {
                min-height: 0;
                font-size: 11px;
                color: #7878aa;
                background: transparent;
                border: 1px solid #3a3a55;
                border-radius: 5px;
                padding: 1px 8px;
            }
            QPushButton#histClearBtn:hover { background: #2d2d45; color: #e05555; }
            #addrList {
                border: 1px solid #3a3a55;
                border-radius: 8px;
                background: #2a2a3e;
                alternate-background-color: #252535;
                color: #d0d0e8;
                font-size: 13px;
            }
            #addrList::item { padding: 3px 6px; }
            #addrList::item:selected { background: #1e3a8a; color: #e0e0ff; }
            QPushButton#clearButton {
                min-height: 28px;
                font-size: 12px;
                color: #7878aa;
                background: transparent;
                border: 1px solid #3a3a55;
                border-radius: 6px;
                padding: 2px 16px;
            }
            QPushButton#clearButton:hover {
                background: #3a1a1a;
                color: #f87171;
                border-color: #6b2020;
            }
            QPushButton#addAddrBtn {
                min-height: 0;
                font-size: 16px;
                font-weight: 600;
                color: #4a6cf7;
                background: transparent;
                border: 1px solid #3a4a88;
                border-radius: 6px;
                padding: 0;
            }
            QPushButton#addAddrBtn:hover { background: #1e2a5a; }
            QLabel#groupBoxTitle {
                font-size: 15px;
                font-weight: 600;
                color: #d0d0f0;
                padding-left: 4px;
            }
            QPushButton#themeMiniBtn {
                min-height: 0;
                font-size: 12px;
                padding: 2px 10px;
                border-radius: 6px;
                border: 1px solid #3a3a55;
                background: #2a2a3e;
                color: #a0a0cc;
            }
            QPushButton#themeMiniBtn:hover {
                background: #1e2a5a;
                border-color: #4a6cf7;
                color: #8899ff;
            }
            QPushButton#fontMiniBtn {
                min-height: 0;
                font-size: 12px;
                font-weight: 700;
                padding: 2px 10px;
                border-radius: 6px;
                border: 1px solid #3a3a55;
                background: #2a2a3e;
                color: #a0a0cc;
            }
            QPushButton#fontMiniBtn:hover {
                background: #1e2a5a;
                border-color: #4a6cf7;
                color: #8899ff;
            }
            #sectionTitle {
                font-size: 12px;
                font-weight: 600;
                color: #8888bb;
                letter-spacing: 0.3px;
            }
            #platformChip {
                border: 1px solid #3a3a55;
                border-radius: 8px;
                background: #222232;
            }
            #platformChip QCheckBox {
                font-size: 13px;
                color: #c0c0e0;
                spacing: 6px;
            }
            #postHeader {
                background: #222232;
                border-bottom: 1px solid #2d2d45;
            }
            #postAvatar {
                background: #4a6cf7;
                color: white;
                font-size: 16px;
                font-weight: 700;
                border-radius: 18px;
            }
            #postAuthor {
                font-size: 13px;
                font-weight: 600;
                color: #d0d0f0;
            }
            #postDate {
                font-size: 11px;
                color: #6868aa;
            }
            QPushButton#postMoreBtn {
                min-height: 0;
                font-size: 16px;
                color: #6868aa;
                background: transparent;
                border: none;
                padding: 0;
                letter-spacing: 2px;
            }
            QPushButton#postMoreBtn:hover { color: #9999cc; }
            #postReactions {
                background: #252535;
                border-top: 1px solid #2d2d45;
            }
            #reactionItem { font-size: 11px; color: #6868aa; }
            QPushButton#themeThumb {
                min-height: 0;
                border: 2px solid #3a3a55;
                border-radius: 6px;
                background: #2a2a3e;
                padding: 0;
                font-size: 12px;
                color: #a0a0cc;
            }
            QPushButton#themeThumb:hover { border-color: #4a6cf7; }
            QPushButton#themeThumb:checked { border: 2px solid #4a6cf7; background: #1e2a5a; }
            QProgressBar#sendProgress {
                border: none;
                border-radius: 2px;
                background: #2a2a3e;
            }
            QProgressBar#sendProgress::chunk {
                background: #4a6cf7;
                border-radius: 2px;
            }
            QPushButton#cancelSendBtn {
                min-height: 0;
                font-size: 11px;
                font-weight: 600;
                color: #f87171;
                background: #2a1a1a;
                border: 1px solid #6b2020;
                border-radius: 5px;
                padding: 1px 10px;
            }
            QPushButton#cancelSendBtn:hover {
                background: #3a1a1a;
                border-color: #e05555;
            }
            QPushButton#cancelSendBtn:disabled {
                color: #555566;
                background: #222232;
                border-color: #333344;
            }
        """


def get_stylesheet() -> str:
    """Возвращает полную QSS-строку стилей приложения."""
    return """
            #maxPosterContent { background: #f3f4f6; }
            QGroupBox {
                font-size: 15px;
                font-weight: 600;
                border: 1px solid #cfd6df;
                border-radius: 10px;
                margin-top: 12px;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 6px;
            }
            #sidePanel {
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #ffffff;
                margin-top: 0;
            }
            QPlainTextEdit, QTextEdit, QLineEdit, QComboBox {
                border: 1px solid #c7d0db;
                border-radius: 8px;
                padding: 8px;
                background: #ffffff;
                font-size: 14px;
            }
            QPushButton {
                min-height: 42px;
                border-radius: 8px;
                border: 1px solid #bfc8d4;
                background: #eef2f7;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton#primaryButton {
                background: #2d6cdf;
                color: white;
                border: none;
                font-weight: 600;
            }
            #previewCard {
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #ffffff;
            }
            #postCard {
                background: #ffffff;
            }
            #postText {
                border: none;
                background: #ffffff;
                font-size: 14px;
                color: #1a1a1a;
                padding: 10px 2px;
            }
            #textContainer {
                border: 1px solid #c7d0db;
                border-radius: 8px;
                background: #ffffff;
            }
            #textContainer QPlainTextEdit {
                border: none;
                border-radius: 8px 8px 0 0;
                padding: 8px;
                background: #ffffff;
                font-size: 14px;
            }
            #emojiButton {
                min-height: 24px;
                border: none;
                background: transparent;
                font-size: 16px;
                padding: 0;
            }
            #emojiButton:hover { background: #eef2f7; border-radius: 4px; }
            #charCounter { font-size: 12px; color: #888; }
            #emojiPicker {
                border: 1px solid #c7d0db;
                border-radius: 10px;
                background: #ffffff;
            }
            #emojiBtn {
                min-height: 0;
                border: none;
                background: transparent;
                font-size: 18px;
                padding: 0;
            }
            #emojiBtn:hover { background: #eef2f7; border-radius: 4px; }
            #versionLabel { font-size: 11px; color: #aab0bb; padding: 2px 0; }
            QPushButton#photoButtonDone {
                background: #eaf7ef;
                color: #1a7a3f;
                border: 1px solid #a8dfc0;
                font-size: 13px;
            }
            #checklistFrame {
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #f8fafc;
            }
            #checklistTitle {
                font-size: 12px;
                font-weight: 600;
                color: #7a8799;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            #previewTitle {
                font-size: 13px;
                font-weight: 600;
                color: #7a8799;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            #checklistItem { font-size: 13px; color: #444; padding: 1px 0; }
            #historyFrame {
                border: 1px solid #e4eaf0;
                border-radius: 10px;
                background: #f8fafc;
            }
            #histEntry {
                background: #ffffff;
                border: 1px solid #eaeff5;
                border-radius: 7px;
                padding: 5px 8px;
            }
            #histEmpty { font-size: 12px; color: #c0c8d4; padding: 4px 0; }
            #histDate { font-size: 11px; color: #b0b8c4; }
            #histText { font-size: 12px; color: #4a5568; }
            #histPlatformFallback { font-size: 11px; color: #6b7280; font-weight: 600; }
            QPushButton#histClearBtn {
                min-height: 0;
                font-size: 11px;
                color: #a0a8b4;
                background: transparent;
                border: 1px solid #dde3ea;
                border-radius: 5px;
                padding: 1px 8px;
            }
            QPushButton#histClearBtn:hover { background: #f0f4f8; color: #e05555; }
            #addrList {
                border: 1px solid #c7d0db;
                border-radius: 8px;
                background: #ffffff;
                alternate-background-color: #f8f9fb;
                font-size: 13px;
            }
            #addrList::item { padding: 3px 6px; }
            #addrList::item:selected { background: #eef4ff; color: #1a1a1a; }
            QPushButton#clearButton {
                min-height: 28px;
                font-size: 12px;
                color: #a0a8b4;
                background: transparent;
                border: 1px solid #dde3ea;
                border-radius: 6px;
                padding: 2px 16px;
            }
            QPushButton#clearButton:hover {
                background: #fff0f0;
                color: #d94040;
                border-color: #f0b0b0;
            }
            QPushButton#addAddrBtn {
                min-height: 0;
                font-size: 16px;
                font-weight: 600;
                color: #2d6cdf;
                background: transparent;
                border: 1px solid #b0c4e8;
                border-radius: 6px;
                padding: 0;
            }
            QPushButton#addAddrBtn:hover { background: #eef4ff; }
            QLabel#groupBoxTitle {
                font-size: 15px;
                font-weight: 600;
                color: #1a1a2e;
                padding-left: 4px;
            }
            QPushButton#themeMiniBtn {
                min-height: 0;
                font-size: 12px;
                padding: 2px 10px;
                border-radius: 6px;
                border: 1px solid #c7d0db;
                background: #f3f4f6;
                color: #555;
            }
            QPushButton#themeMiniBtn:hover {
                background: #eef4ff;
                border-color: #2d6cdf;
                color: #2d6cdf;
            }
            QPushButton#fontMiniBtn {
                min-height: 0;
                font-size: 12px;
                font-weight: 700;
                padding: 2px 10px;
                border-radius: 6px;
                border: 1px solid #c7d0db;
                background: #f3f4f6;
                color: #555;
            }
            QPushButton#fontMiniBtn:hover {
                background: #eef4ff;
                border-color: #2d6cdf;
                color: #2d6cdf;
            }
            /* ── Секция платформ ──────────────────────────────────── */
            #sectionTitle {
                font-size: 12px;
                font-weight: 600;
                color: #6b7280;
                letter-spacing: 0.3px;
            }
            #platformChip {
                border: 1px solid #d1d9e0;
                border-radius: 8px;
                background: #f8fafc;
            }
            #platformChip QCheckBox {
                font-size: 13px;
                color: #1f2937;
                spacing: 6px;
            }
            /* ── Шапка поста (preview) ───────────────────────────── */
            #postHeader {
                background: #f8fafc;
                border-bottom: 1px solid #f0f4f8;
            }
            #postAvatar {
                background: #3b82f6;
                color: white;
                font-size: 16px;
                font-weight: 700;
                border-radius: 18px;
            }
            #postAuthor {
                font-size: 13px;
                font-weight: 600;
                color: #1f2937;
            }
            #postDate {
                font-size: 11px;
                color: #9ca3af;
            }
            QPushButton#postMoreBtn {
                min-height: 0;
                font-size: 16px;
                color: #9ca3af;
                background: transparent;
                border: none;
                padding: 0;
                letter-spacing: 2px;
            }
            QPushButton#postMoreBtn:hover {
                color: #6b7280;
            }
            /* ── Реакции поста ────────────────────────────────────── */
            #postReactions {
                background: #ffffff;
                border-top: 1px solid #f0f4f8;
            }
            #reactionItem {
                font-size: 11px;
                color: #9ca3af;
            }
            QPushButton#themeThumb {
                min-height: 0;
                border: 2px solid #dde3ea;
                border-radius: 6px;
                background: #f0f2f5;
                padding: 0;
                font-size: 12px;
                color: #555;
            }
            QPushButton#themeThumb:hover { border-color: #2d6cdf; }
            QPushButton#themeThumb:checked { border: 2px solid #2d6cdf; background: #eef4ff; }
            QProgressBar#sendProgress {
                border: none;
                border-radius: 2px;
                background: #e8edf3;
            }
            QProgressBar#sendProgress::chunk {
                background: #2d6cdf;
                border-radius: 2px;
            }
            QPushButton#cancelSendBtn {
                min-height: 0;
                font-size: 11px;
                font-weight: 600;
                color: #c0392b;
                background: #fff5f5;
                border: 1px solid #f5c6c6;
                border-radius: 5px;
                padding: 1px 10px;
            }
            QPushButton#cancelSendBtn:hover {
                background: #ffe0e0;
                border-color: #e05555;
            }
            QPushButton#cancelSendBtn:disabled {
                color: #aaa;
                background: #f5f5f5;
                border-color: #e0e0e0;
            }
        """
