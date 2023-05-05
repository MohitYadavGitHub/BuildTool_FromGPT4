import time

time.sleep(2)
with open('test.txt', 'w') as fw:
    fw.write ('whatever text')