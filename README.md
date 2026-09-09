# Movie Shot Analyzer V5.7.2

V5.7.1をベースに、パースの2本目確定後は1本目と同じ細い線幅へ戻るように調整しました。パース操作用の白いアンカーポイントも直径14pxから10pxへ小型化しています。基本ガイドの交点○、追加構図の編集アンカー、トンネル8アンカーなど既存仕様は維持しています。

# Movie Shot Analyzer V5.7.1

Perspective interaction fix:
- Only line 1 is shown initially for each VP.
- Drag its two white anchors independently.
- After both anchors have been placed, line 2 is created and shown automatically.
- The hidden second line no longer changes the VP/eye level while line 1 is being positioned.
- VP is solved from the two visible user-positioned lines.
- Existing composition guide behavior is retained.
