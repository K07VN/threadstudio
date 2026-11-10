---
title: Fluent Blue ThemedMixin MRO and Qt label mnemonics
author: K07VN
date: 2026-11-10
id: l-29e0c5fa
scope: repo
status: active
src: agent
touches:
  - src/threadstudio/ui/widgets/stage_canvas.py
  - src/threadstudio/ui/main_window.py
---

In src/threadstudio/ui/widgets/stage_canvas.py (InteractiveStageCanvas), ThemedMixin must precede QWidget in Python MRO (class InteractiveStageCanvas(ThemedMixin, QWidget)) because fluent_blue's ThemedMixin.showEvent calls super().showEvent(event) expecting QWidget next in MRO. Also avoid single ampersand (&) in Qt labels inside src/threadstudio/ui/main_window.py (ThreadsStudioWindow) because Qt interprets &X as a keyboard mnemonic underline.
