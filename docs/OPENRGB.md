# OpenRGB programs

EVision RGB can answer as an **OpenRGB SDK server**. Programs that drive lights through OpenRGB can then light the
AK820 through this app:

- **Artemis** (Artemis RGB): enable its OpenRGB device provider and point it at `127.0.0.1:6742`.
- **SignalRGB**: with the community "SignalRGB to OpenRGB" bridge plugin, SignalRGB's effects reach the keyboard.
- **Home Assistant**: its OpenRGB integration, if Home Assistant runs on this PC (the server only listens on
  `127.0.0.1`, so other computers can't connect).
- **Scripts**: openrgb-python, OpenRGB.NET and other SDK clients.
- **OpenRGB itself**, as a client of this app. Don't let OpenRGB detect the keyboard directly: it talks to this model
  with the wrong protocol (the app warns about that).

Turn it on in **Other Settings → OpenRGB programs** (port 6742 by default, like OpenRGB). If OpenRGB itself is
running, it already uses that port: close it or pick another port.

## What clients see
- One device, **AJAZZ AK820**: a keyboard with a 6 × 17 key matrix and OpenRGB's standard key names (`Key: Escape`,
  `Key: Left Shift`, ...), so clients place the keys correctly.
- **Direct** mode: the client's colours become the live lighting. Caps Lock and the other indicators stay on top, and
  colours reach the LEDs as sent (no gamma), like OpenRGB does.
- The other modes are the keyboard's **built-in effects**. Choosing one stores it on the keyboard, like Light Settings.
- The profiles saved in this app are listed and can be loaded.

## Who wins
- When the last client disconnects, your own lighting comes back.
- If you pick something in this app while a client is connected, your choice wins until the client switches to Direct
  again.
- Game lighting doesn't take over while an OpenRGB program has the lighting.

## Details
Protocol versions 0 to 4 (newer clients negotiate down), written from OpenRGB's published SDK documentation
(`Documentation/OpenRGBSDK.md` in the OpenRGB project). Packets larger than 1 MB or without the `ORGB` magic close
that connection; packets for other devices or unknown commands are ignored.
