# Bundled font

`NotoSansSC-Bold.ttf` — Noto Sans SC, weight 700, generated from the
variable font shipped by [googlefonts/noto-cjk](https://github.com/notofonts/noto-cjk)
using fontTools' `instantiateVariableFont`. The outline is Bold; the internal
name table still reads "Thin" because instancing does not rewrite names —
this is harmless and only visible in font inspectors.

- License: SIL Open Font License 1.1, full text in [OFL.txt](OFL.txt).
- Copyright: © 2014-2021 Adobe, Reserved Font Name "Source".
- The plugin loads this file first, so cards look the same on Windows,
  Linux and macOS. Set `FURCON_FONT` to an absolute path to use your own
  font instead. If this file is missing, the plugin falls back to system
  fonts (which may be proprietary and may not cover all CJK characters).
