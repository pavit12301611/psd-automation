def mandel(cr, ci):
    zr = 0.0
    zi = 0.0
    n = 0
    while n < 80:
        zr2 = zr * zr
        zi2 = zi * zi
        if zr2 + zi2 > 4.0:
            return n
        zi = 2.0 * zr * zi + ci
        zr = zr2 - zi2 + cr
        n += 1
    return 80

total = 0
for y in range(200):
    for x in range(200):
        cr = 3.5 * x / 200.0 - 2.5
        ci = 2.5 * y / 200.0 - 1.25
        total += mandel(cr, ci)
print(total)
