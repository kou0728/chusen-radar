"""Mobilerun Portal の中継サーバー（ngrok の代わり）。

端末（Portal）は VPS 上のこのサーバーへ外向きにつなぎに来て、
iMac などは Tailscale 経由の管理APIで、つながっている端末を操作します。
使い方は mobilerun_hub/README.md を見てください。
"""
