import pyautogui
import sys
import time

while True:
    time.sleep(2)
    pyautogui.click()
    time.sleep(0.5)
    pyautogui.press('up')
    time.sleep(0.5)
    pyautogui.press('enter')