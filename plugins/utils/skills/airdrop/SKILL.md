---
name: airdrop
description: Send a file from this Mac to a nearby iPhone, iPad or other Apple device with AirDrop. Use when the user says to airdrop something, send this to my phone, get this onto my iPad, or drop this to my device, and when something you made should end up on a device in their hand. Opens the AirDrop picker with the files loaded, the person clicks the device, and it waits for the transfer to finish.
---

Apple lets a program open the AirDrop picker but not choose who receives, so the person always picks the device. That single click is the design. Do not try to automate it.

Run `scripts/airdrop.sh <file> [more files]` with absolute paths and tell the person to click their device in the window that appears. A device on the same Apple ID accepts by itself; any other device has to tap Accept. The first run compiles a small helper, which takes a few seconds and needs the Xcode command line tools. `--timeout <seconds>` sets how long to wait, 300 by default.

Report what the exit code says: 0 the transfer finished, 2 a file is missing, 4 nothing finished in time (they did not click, or the other device did not accept), 5 AirDrop failed or is unavailable. Wi-Fi and Bluetooth must both be on, and the device must be awake and able to receive.

If AirDrop will not do, serve the file on the shared network: copy it alone into a new temporary folder, run `python3 -m http.server 8770 --bind "$(ipconfig getifaddr en0)"` there, give the person the address, and stop the server once they have it. Never serve the folder the file came from, since anyone on the network can read what is served.
