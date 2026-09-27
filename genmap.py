"""Rebuild mapsdata entries from .map files, in the same format as the teammate's maps_data.hpp."""
import re, sys
ALPH = "!#$%&'()*+,-./0123456789:;<=>@ABCDEFGHIJKLMNOPQRSTUVWXYZ[]^_`abcdefghijklmnopqrstuvwxyz{|}~"

def parse(path):
    L = open(path).read().split('\n')
    W, H = map(int, L[0].split()[1:3]); R = W + 1
    name, sym = None, ''
    tiles = {}; E = {}; drag = []
    for l in L:
        f = l.split()
        if not f: continue
        if f[0] == 'MAP_NAME': name = l[len('MAP_NAME '):].strip()
        elif f[0] == 'SYMMETRY': sym = f[1]
        elif f[0] == 'TILE': tiles[(int(f[1]), int(f[2]))] = (int(f[3]), int(f[4]))
        elif f[0] == 'EDGE': E[int(f[1])] = (int(f[2]), int(f[3]))
        elif f[0] == 'DRAGON': drag.append(list(map(int, f[1:])))
    return name, W, H, sym, tiles, E, drag

def build(path, idx):
    name, W, H, sym, tiles, E, drag = parse(path)
    R = W + 1
    pal = []
    tchars = []
    for y in range(H):
        for x in range(W):
            g = tiles.get((x, y), (0, 0))
            if g not in pal: pal.append(g)
            tchars.append(ALPH[pal.index(g)])
    echars = []; portals = []
    for y in range(H):
        for x in range(W):
            t = y * W + x
            for side, row in ((0, 2 * y), (1, 2 * y + 1)):   # north edge row, west edge row
                k, pid = E.get(row * R + x, (0, -1))
                echars.append('.' if k == 0 else ('w' if k == 1 else 'p'))
                if k == 2: portals += [t, side, pid]
    dflat = []
    for d in drag: dflat += d
    return dict(name=name, W=W, H=H, sym=sym, pal=pal, tiles=''.join(tchars), edges=''.join(echars),
                portals=portals, drag=dflat, nd=len(drag))

def emit(m, i):
    def chunks(s): return '\n'.join('        "%s"' % s[j:j+100] for j in range(0, len(s), 100))
    pal = ', '.join(f'{a}, {b}' for a, b in m['pal'])
    out = f"static const short pal{i}[] = {{{pal}}};\n"
    out += f"static const int portals{i}[] = {{{', '.join(map(str, m['portals'])) or '0'}}};\n"
    out += f"static const int dragons{i}[] = {{{', '.join(map(str, m['drag']))}}};\n"
    out += f"static const char tiles{i}[] =\n{chunks(m['tiles'])};\n"
    out += f"static const char edges{i}[] =\n{chunks(m['edges'])};\n"
    row = f'    {{"{m["name"]}", {m["W"]}, {m["H"]}, "{m["sym"]}", {len(m["pal"])}, pal{i}, tiles{i}, edges{i}, {len(m["portals"])//3}, portals{i}, {m["nd"]}, dragons{i}}},'
    return out, row

if __name__ == '__main__':
    # validation: compare against an existing embedded map
    s = open(sys.argv[1]).read()
    for path, i in [(p.split(':')[0], int(p.split(':')[1])) for p in sys.argv[2:]]:
        m = build(path, i)
        def grab(tag):
            blk = re.search(tag + str(i) + r'\[\] =(.*?);', s, re.S).group(1)
            return ''.join(re.findall(r'"(.*?)"', blk))
        pal = [int(v) for v in re.search(r'pal%d\[\] = \{(.*?)\}' % i, s).group(1).split(',')]
        print(path, 'tiles', grab('tiles') == m['tiles'], 'edges', grab('edges') == m['edges'],
              'pal', pal == [v for ab in m['pal'] for v in ab])
