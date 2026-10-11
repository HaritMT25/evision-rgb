# Adding another EVision keyboard

Many keyboards use the same EVision controller and lighting protocol as the AJAZZ AK820: USB vendor id `0x320F`,
a vendor HID collection with usage page `0xFF1C` / usage `0x92`, report id 4 and 64-byte packets
([PROTOCOL.md](PROTOCOL.md)). If yours does, it can probably be supported. What differs between models is the key
layout (which LED slot sits under which key, and where the key is) and a few firmware quirks.

Today the app knows one layout, the AK820's (`evision_rgb/layout.py`). Supporting a second keyboard means adding
its layout and quirks and choosing them by USB product id. That work is easy once the information below exists.

## What to collect (all read-only)

These steps only read from the keyboard, with the same queries the vendor app uses. Nothing is written.

1. **USB ids and interfaces.** In PowerShell:
   `Get-PnpDevice -PresentOnly | Where-Object InstanceId -like 'HID\VID_320F*' | Select-Object FriendlyName, InstanceId`
   This lists the product id (`PID_xxxx`) and the HID collections (`MI_01&Col04` and so on).
2. **Capabilities and current settings.** Close the vendor app and other RGB software, then run `evrgb info`.
   It prints the capability block (cmd 0x03: the second byte after `aa 55` is the number of LED slots) and the
   stored effect settings. If it says the interface was not found, the keyboard uses a different collection.
3. **Key map.** `evrgb info` uses only reads. The key map (cmd 0x07, LED slot to key) is read by the
   app's backup on its first start: look in `%APPDATA%\EVisionRGB\backup\keymap.bin`. Attach that file.
4. **Physical layout.** A photo of the keyboard from above, and the model's name and firmware version as the vendor
   app shows them.

Please don't send other commands to find out more. The app's allow-list blocks everything except the documented
lighting commands; firmware, key-map and macro writes can brick or reprogram a keyboard.

## USB captures (optional, very useful)

A capture of the vendor app shows exactly which packets it sends for each setting.

1. Install [USBPcap](https://desowin.org/usbpcap/) and [Wireshark](https://www.wireshark.org/).
2. Start a capture on the root hub the keyboard is on, then in the vendor app change **one** setting at a time
   (effect, brightness, speed, colour, a custom per-key colour) and note what you changed and when.
3. Filter to the keyboard's vendor interface (`usb.idVendor == 0x320f`, report id 4) before saving.

**Don't type while capturing**: a capture of the keyboard can contain your key presses. Use the mouse in the vendor
app, and filter the capture to the lighting interface before sharing it.

## What a new model needs in code

- The product id next to the AK820's in `evision_rgb/protocol.py` (`find_path`) and the device table in the README.
- A layout: LED slot, x, y per key. `scripts/gen_layout.py` builds one from the key map plus key positions.
- Its quirks in `docs/PROTOCOL.md` and the simulator (`tests/fake_device.py`): brightness range, which custom-colour
  area the firmware displays, how long direct mode lasts without frames.
- Hardware checks: `tests/test_hardware.py` with `AK820_HW=1` confirms the simulator's assumptions on the real
  keyboard and restores its state afterwards.

Open an issue with what you collected and we'll take it from there.
