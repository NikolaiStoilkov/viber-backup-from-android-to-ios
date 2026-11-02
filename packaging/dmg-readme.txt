VIBER TRANSFER – INSTALL

1. Drag "Viber Transfer" onto the Applications folder.

2. First launch. This build is not notarized by Apple, so macOS blocks it the first time:
   - Double-click Viber Transfer in Applications and click "Done" in the warning.
   - Open System Settings > Privacy & Security, scroll down and click "Open Anyway"
     next to "Viber Transfer", then confirm.
   You only have to do this once.

3. Install the device tools (once), in Terminal:
       brew install --cask android-platform-tools
       brew install libimobiledevice
   (Homebrew: https://brew.sh). Everything else is inside the app.

4. Open Viber Transfer. Connect both phones, check that both cards say "Connected",
   and click Start Transfer. Settings are under Viber Transfer > Settings (Cmd+,).
   The app never uses the internet.

Backups:  ~/Documents/ViberTransfer/backups/
Log file: ~/Library/Logs/ViberTransfer/engine.log

Important: chats cannot be imported into Viber on the iPhone; the app copies a readable
archive (transcripts + attachments) to the iPhone's Files app. Root access is needed on the
Android phone. See the README in the project for full details and limitations.
