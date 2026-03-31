import ctypes
import sys

# Create a popup message box
ctypes.windll.user32.MessageBoxW(0, "You got hacked!", "HACKED", 0)

sys.exit()