Output assets/scopecreep-demo.gif

Set Shell "bash"
Set FontSize 19
Set Width 1200
Set Height 720
Set Theme "Catppuccin Latte"
Set TypingSpeed 42ms
Set Padding 28
Set BorderRadius 12
Set WindowBar "Colorful"

Hide
Type "source .venv/bin/activate && clear"
Enter
Show

Sleep 700ms
Type "scopecreep check pallets/click#2"
Enter
Wait+Screen@40s /Judged in/
Sleep 3s
