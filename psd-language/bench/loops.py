total = 0
i = 0
while i < 1000000000:
    total += i - (i // 2) * 2
    i += 1
print(total)
