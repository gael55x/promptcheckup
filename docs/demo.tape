# Records docs/assets/demo.gif for the promptcheckup README.
# Uses VHS (https://github.com/charmbracelet/vhs). Run from the promptcheckup repo root:
#   vhs docs/demo.tape
# No API key needed: both commands run offline.

Output docs/assets/demo.gif
Set FontSize 18
Set Width 1200
Set Height 560
Set Padding 24
Set TypingSpeed 40ms
Set Theme "GitHub Dark"

Hide
Type "export PYTHONPATH=$PWD/src && clear"
Enter
Show

Type "# Prompt B really is better. Does comparing two weeks notice?"
Enter
Sleep 600ms
Type "python3 examples/planted/planted.py"
Enter
Sleep 3s

Type "# Now the gate, as it runs in CI"
Enter
Sleep 600ms
Type "cd examples/connect/plain && python3 -m promptcheckup.cli.main gate --old old-system.md --new prompts/system.md --yes --out /tmp/report.md > /dev/null; echo \"exit code $?\"; head -1 /tmp/report.md"
Enter
Sleep 4s
Hide
Type "rm -f /tmp/report.md result.json; cd ../../.."
Enter
