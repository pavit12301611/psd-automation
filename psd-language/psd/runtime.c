#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

/*
 * PSD runtime library.
 *
 * Values:
 *   int    -> int64_t (i64)
 *   float  -> double
 *   bool   -> int8_t (0/1)
 *   str    -> psd_str*  (refcounted, immutable)
 *   [T]    -> psd_list* (refcounted, dynamic; elements stored as 8-byte slots)
 *
 * Reference counting rules (mirrored by the code generator):
 *   - every str/list value flowing around is an owned reference
 *   - builtin "borrowing" functions (print/len/push/list_get/list_set/str_eq...)
 *     do NOT consume their arguments; the caller decrefs its temporaries
 *   - psd_list_pop transfers the element reference to the caller
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <strings.h>
#include <ctype.h>
#include <time.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

/* ---- panic ------------------------------------------------------------- */

void psd_panic(const char *msg) {
    fprintf(stderr, "psd: runtime error: %s\n", msg);
    fflush(stderr);
    exit(1);
}

/* ---- strings ----------------------------------------------------------- */

typedef struct psd_str {
    int64_t rc;
    int64_t len;
    char data[];          /* NUL-terminated for convenience */
} psd_str;

static void *psd_alloc(int64_t bytes) {
    void *p = malloc((size_t)bytes);
    if (!p) psd_panic("out of memory");
    return p;
}

psd_str *psd_str_new(const char *bytes, int64_t len) {
    psd_str *s = (psd_str *)psd_alloc((int64_t)sizeof(psd_str) + len + 1);
    s->rc = 1;
    s->len = len;
    if (len > 0 && bytes) memcpy(s->data, bytes, (size_t)len);
    s->data[len] = '\0';
    return s;
}

void psd_str_incref(psd_str *s) {
    if (s) s->rc++;
}

void psd_str_decref(psd_str *s) {
    if (!s) return;
    if (--s->rc <= 0) free(s);
}

int64_t psd_str_len(psd_str *s) {
    return s ? s->len : 0;
}

psd_str *psd_str_concat(psd_str *a, psd_str *b) {
    int64_t la = a ? a->len : 0, lb = b ? b->len : 0;
    psd_str *s = (psd_str *)psd_alloc((int64_t)sizeof(psd_str) + la + lb + 1);
    s->rc = 1;
    s->len = la + lb;
    if (la) memcpy(s->data, a->data, (size_t)la);
    if (lb) memcpy(s->data + la, b->data, (size_t)lb);
    s->data[la + lb] = '\0';
    return s;
}

int64_t psd_str_eq(psd_str *a, psd_str *b) {
    if (a == b) return 1;
    if (!a || !b) return 0;
    if (a->len != b->len) return 0;
    return memcmp(a->data, b->data, (size_t)a->len) == 0 ? 1 : 0;
}

/* ---- conversions ------------------------------------------------------- */

static void psd_fmt_double(double v, char *buf, size_t n) {
    if (isfinite(v) && v == floor(v) && fabs(v) < 1e16) {
        snprintf(buf, n, "%.1f", v);
        return;
    }
    /* shortest representation that round-trips exactly */
    for (int prec = 15; prec <= 17; prec++) {
        snprintf(buf, n, "%.*g", prec, v);
        if (strtod(buf, NULL) == v) return;
    }
}

psd_str *psd_str_from_i64(int64_t v) {
    char buf[32];
    int n = snprintf(buf, sizeof buf, "%lld", (long long)v);
    return psd_str_new(buf, n);
}

psd_str *psd_str_from_f64(double v) {
    char buf[40];
    psd_fmt_double(v, buf, sizeof buf);
    return psd_str_new(buf, (int64_t)strlen(buf));
}

psd_str *psd_str_from_bool(int8_t v) {
    return v ? psd_str_new("true", 4) : psd_str_new("false", 5);
}

int64_t psd_str_to_i64(psd_str *s) {
    if (!s || s->len == 0) psd_panic("int(): cannot convert empty string to int");
    char *end = NULL;
    long long v = strtoll(s->data, &end, 10);
    if (end != s->data + s->len) psd_panic("int(): invalid integer literal");
    return (int64_t)v;
}

int64_t psd_float_to_i64(double d) {
    /* checked conversion: truncates toward zero, panics on out-of-range */
    if (d != d) psd_panic("int(): cannot convert nan to int");
    if (!(d >= -9223372036854775808.0 && d < 9223372036854775808.0))
        psd_panic("int(): float out of int range");
    return (int64_t)d;
}

double psd_str_to_f64(psd_str *s) {
    if (!s || s->len == 0) psd_panic("float(): cannot convert empty string to float");
    char *end = NULL;
    double v = strtod(s->data, &end);
    if (end != s->data + s->len) psd_panic("float(): invalid float literal");
    return v;
}

/* ---- lists ------------------------------------------------------------- */

enum { PSD_ELEM_SCALAR = 0, PSD_ELEM_STR = 1, PSD_ELEM_LIST = 2,
       PSD_ELEM_MAP = 3, PSD_ELEM_JSON = 4, PSD_ELEM_FLOAT = 5, PSD_ELEM_BOOL = 6 };

/* forward declarations for recursively-structured value kinds */
struct psd_json;
typedef struct psd_json psd_json;
void psd_json_incref(psd_json *j);
void psd_json_decref(psd_json *j);
psd_str *psd_json_encode(psd_json *j);
struct psd_map;
typedef struct psd_map psd_map;
void psd_map_incref(psd_map *m);
void psd_map_decref(psd_map *m);

typedef struct psd_list {
    int64_t rc;
    int64_t len;
    int64_t cap;
    int64_t kind;         /* element kind: scalar / str / list */
    int64_t *data;        /* 8-byte slots */
} psd_list;

psd_list *psd_list_new(int64_t elem_kind, int64_t hint_cap) {
    psd_list *l = (psd_list *)psd_alloc(sizeof(psd_list));
    l->rc = 1;
    l->len = 0;
    l->cap = hint_cap > 0 ? hint_cap : 4;
    l->kind = elem_kind;
    l->data = (int64_t *)psd_alloc(l->cap * (int64_t)sizeof(int64_t));
    return l;
}

void psd_list_incref(psd_list *l) {
    if (l) l->rc++;
}

static void psd_slot_incref(psd_list *l, int64_t slot) {
    if (l->kind == PSD_ELEM_STR) psd_str_incref((psd_str *)(intptr_t)slot);
    else if (l->kind == PSD_ELEM_LIST) psd_list_incref((psd_list *)(intptr_t)slot);
    else if (l->kind == PSD_ELEM_MAP) psd_map_incref((psd_map *)(intptr_t)slot);
    else if (l->kind == PSD_ELEM_JSON) psd_json_incref((psd_json *)(intptr_t)slot);
}

void psd_list_decref(psd_list *l);

static void psd_slot_decref(psd_list *l, int64_t slot) {
    if (l->kind == PSD_ELEM_STR) psd_str_decref((psd_str *)(intptr_t)slot);
    else if (l->kind == PSD_ELEM_LIST) psd_list_decref((psd_list *)(intptr_t)slot);
    else if (l->kind == PSD_ELEM_MAP) psd_map_decref((psd_map *)(intptr_t)slot);
    else if (l->kind == PSD_ELEM_JSON) psd_json_decref((psd_json *)(intptr_t)slot);
}

void psd_list_decref(psd_list *l) {
    if (!l) return;
    if (--l->rc <= 0) {
        if (l->kind != PSD_ELEM_SCALAR) {
            for (int64_t i = 0; i < l->len; i++) psd_slot_decref(l, l->data[i]);
        }
        free(l->data);
        free(l);
    }
}

int64_t psd_list_len(psd_list *l) {
    return l ? l->len : 0;
}

void psd_list_push(psd_list *l, int64_t value) {
    if (!l) psd_panic("push(): list is null");
    if (l->len == l->cap) {
        l->cap *= 2;
        l->data = (int64_t *)realloc(l->data, (size_t)l->cap * sizeof(int64_t));
        if (!l->data) psd_panic("out of memory");
    }
    psd_slot_incref(l, value);
    l->data[l->len++] = value;
}

static void psd_list_check(psd_list *l, int64_t idx) {
    if (!l) psd_panic("list is null");
    if (idx < 0 || idx >= l->len) psd_panic("index out of range");
}

int64_t psd_list_get(psd_list *l, int64_t idx) {
    psd_list_check(l, idx);
    return l->data[idx];
}

void psd_list_set(psd_list *l, int64_t idx, int64_t value) {
    psd_list_check(l, idx);
    psd_slot_incref(l, value);
    psd_slot_decref(l, l->data[idx]);
    l->data[idx] = value;
}

int64_t psd_list_pop(psd_list *l) {
    if (!l) psd_panic("pop(): list is null");
    if (l->len == 0) psd_panic("pop(): list is empty");
    return l->data[--l->len];   /* reference transferred to caller */
}

/* ---- integer division / modulo (checked) ------------------------------- */

int64_t psd_div_i64(int64_t a, int64_t b) {
    if (b == 0) psd_panic("division by zero");
    if (a == INT64_MIN && b == -1) psd_panic("integer overflow in division");
    return a / b;
}

int64_t psd_mod_i64(int64_t a, int64_t b) {
    if (b == 0) psd_panic("modulo by zero");
    if (a == INT64_MIN && b == -1) psd_panic("integer overflow in modulo");
    return a % b;
}

/* ---- printing ---------------------------------------------------------- */

void psd_print_i64(int64_t v) {
    printf("%lld", (long long)v);
}

void psd_print_f64(double v) {
    char buf[40];
    psd_fmt_double(v, buf, sizeof buf);
    fputs(buf, stdout);
}

void psd_print_bool(int8_t v) {
    fputs(v ? "true" : "false", stdout);
}

void psd_print_str(psd_str *s) {
    if (s && s->len > 0) fwrite(s->data, 1, (size_t)s->len, stdout);
}

void psd_print_space(void) {
    fputc(' ', stdout);
}

void psd_print_nl(void) {
    fputc('\n', stdout);
}

/* ====================================================================== */
/* v0.2: string methods                                                   */
/* ====================================================================== */

psd_str *psd_str_slice(psd_str *s, int64_t lo, int64_t hi); /* fwd */

psd_str *psd_str_upper(psd_str *s) {
    psd_str *r = psd_str_new(s ? s->data : "", s ? s->len : 0);
    for (int64_t i = 0; i < r->len; i++) {
        char c = r->data[i];
        if (c >= 'a' && c <= 'z') r->data[i] = (char)(c - 32);
    }
    return r;
}

psd_str *psd_str_lower(psd_str *s) {
    psd_str *r = psd_str_new(s ? s->data : "", s ? s->len : 0);
    for (int64_t i = 0; i < r->len; i++) {
        char c = r->data[i];
        if (c >= 'A' && c <= 'Z') r->data[i] = (char)(c + 32);
    }
    return r;
}

psd_str *psd_str_trim(psd_str *s) {
    if (!s) return psd_str_new("", 0);
    int64_t lo = 0, hi = s->len;
    while (lo < hi && (s->data[lo] == ' ' || s->data[lo] == '\t' ||
                       s->data[lo] == '\n' || s->data[lo] == '\r')) lo++;
    while (hi > lo && (s->data[hi - 1] == ' ' || s->data[hi - 1] == '\t' ||
                       s->data[hi - 1] == '\n' || s->data[hi - 1] == '\r')) hi--;
    return psd_str_new(s->data + lo, hi - lo);
}

int64_t psd_str_contains(psd_str *s, psd_str *needle) {
    if (!s || !needle) return 0;
    if (needle->len == 0) return 1;
    if (needle->len > s->len) return 0;
    for (int64_t i = 0; i + needle->len <= s->len; i++)
        if (memcmp(s->data + i, needle->data, (size_t)needle->len) == 0) return 1;
    return 0;
}

int64_t psd_str_starts_with(psd_str *s, psd_str *p) {
    if (!s || !p || p->len > s->len) return 0;
    return memcmp(s->data, p->data, (size_t)p->len) == 0 ? 1 : 0;
}

int64_t psd_str_ends_with(psd_str *s, psd_str *p) {
    if (!s || !p || p->len > s->len) return 0;
    return memcmp(s->data + s->len - p->len, p->data, (size_t)p->len) == 0 ? 1 : 0;
}

psd_str *psd_str_replace(psd_str *s, psd_str *from, psd_str *to) {
    if (!s) return psd_str_new("", 0);
    if (!from || from->len == 0) return psd_str_new(s->data, s->len);
    int64_t cap = s->len + 16, n = 0;
    char *buf = (char *)psd_alloc(cap);
    for (int64_t i = 0; i < s->len; ) {
        if (i + from->len <= s->len &&
            memcmp(s->data + i, from->data, (size_t)from->len) == 0) {
            while (n + to->len + (s->len - i) + 1 > cap) { cap *= 2; buf = (char *)realloc(buf, (size_t)cap); if (!buf) psd_panic("out of memory"); }
            memcpy(buf + n, to->data, (size_t)to->len);
            n += to->len;
            i += from->len;
        } else {
            if (n + 2 > cap) { cap *= 2; buf = (char *)realloc(buf, (size_t)cap); if (!buf) psd_panic("out of memory"); }
            buf[n++] = s->data[i++];
        }
    }
    psd_str *r = psd_str_new(buf, n);
    free(buf);
    return r;
}

psd_str *psd_str_repeat(psd_str *s, int64_t times) {
    if (times < 0) psd_panic("repeat(): count must be >= 0");
    if (!s || s->len == 0 || times == 0) return psd_str_new("", 0);
    if (s->len > 0 && times > (1LL << 30) / s->len) psd_panic("repeat(): result too large");
    psd_str *r = (psd_str *)psd_alloc((int64_t)sizeof(psd_str) + s->len * times + 1);
    r->rc = 1;
    r->len = s->len * times;
    for (int64_t i = 0; i < times; i++)
        memcpy(r->data + i * s->len, s->data, (size_t)s->len);
    r->data[r->len] = '\0';
    return r;
}

psd_list *psd_str_split(psd_str *s, psd_str *sep);

/* ====================================================================== */
/* v0.2: lists of str helpers, maps                                       */
/* ====================================================================== */

int64_t psd_list_contains(psd_list *l, int64_t value) {
    if (!l) return 0;
    for (int64_t i = 0; i < l->len; i++) {
        if (l->kind == PSD_ELEM_STR) {
            if (psd_str_eq((psd_str *)(intptr_t)l->data[i], (psd_str *)(intptr_t)value)) return 1;
        } else if (l->kind == PSD_ELEM_LIST || l->kind == PSD_ELEM_MAP ||
                   l->kind == PSD_ELEM_JSON) {
            if (l->data[i] == value) return 1;
        } else {
            if (l->data[i] == value) return 1;
        }
    }
    return 0;
}

void psd_list_reverse(psd_list *l) {
    if (!l) return;
    for (int64_t i = 0, j = l->len - 1; i < j; i++, j--) {
        int64_t t = l->data[i];
        l->data[i] = l->data[j];
        l->data[j] = t;
    }
}

psd_str *psd_str_from_slot_repr(int64_t slot, int64_t kind);

psd_str *psd_list_join(psd_list *l, psd_str *sep) {
    int64_t cap = 32, n = 0;
    char *buf = (char *)psd_alloc(cap);
    buf[0] = 0;
    for (int64_t i = 0; i < (l ? l->len : 0); i++) {
        psd_str *piece = psd_str_from_slot_repr(l->data[i], l->kind);
        int64_t need = n + piece->len + (i ? (sep ? sep->len : 0) : 0) + 1;
        while (need > cap) { cap *= 2; buf = (char *)realloc(buf, (size_t)cap); if (!buf) psd_panic("out of memory"); }
        if (i && sep && sep->len) { memcpy(buf + n, sep->data, (size_t)sep->len); n += sep->len; }
        memcpy(buf + n, piece->data, (size_t)piece->len);
        n += piece->len;
        psd_str_decref(piece);
    }
    psd_str *r = psd_str_new(buf, n);
    free(buf);
    return r;
}

psd_str *psd_str_from_slot_repr(int64_t slot, int64_t kind) {
    if (kind == PSD_ELEM_STR) return psd_str_new(((psd_str *)(intptr_t)slot)->data,
                                                ((psd_str *)(intptr_t)slot)->len);
    if (kind == PSD_ELEM_FLOAT) {
        double d;
        memcpy(&d, &slot, sizeof(double));
        return psd_str_from_f64(d);
    }
    if (kind == PSD_ELEM_BOOL) return psd_str_from_bool((int8_t)(slot & 1));
    if (kind == PSD_ELEM_JSON) return psd_json_encode((psd_json *)(intptr_t)slot);
    return psd_str_from_i64(slot);
}

/* maps: str -> slot, insertion-ordered --------------------------------- */

psd_list *psd_str_split(psd_str *s, psd_str *sep) {
    if (!s) return psd_list_new(PSD_ELEM_STR, 1);
    if (!sep || sep->len == 0) psd_panic("split(): separator must not be empty");
    psd_list *out = psd_list_new(PSD_ELEM_STR, 8);
    int64_t start = 0;
    for (int64_t i = 0; i + sep->len <= s->len; ) {
        if (memcmp(s->data + i, sep->data, (size_t)sep->len) == 0) {
            psd_str *piece = psd_str_new(s->data + start, i - start);
            psd_list_push(out, (int64_t)(intptr_t)piece);
            psd_str_decref(piece);
            i += sep->len;
            start = i;
        } else {
            i++;
        }
    }
    psd_str *last = psd_str_new(s->data + start, s->len - start);
    psd_list_push(out, (int64_t)(intptr_t)last);
    psd_str_decref(last);
    return out;
}

typedef struct psd_map {
    int64_t rc;
    int64_t len;
    int64_t cap;
    int64_t kind;          /* value element kind */
    psd_str **keys;
    int64_t *vals;
} psd_map;

void psd_list_decref(psd_list *l);

void psd_map_incref(psd_map *m) { if (m) m->rc++; }

void psd_map_decref(psd_map *m) {
    if (!m) return;
    if (--m->rc <= 0) {
        for (int64_t i = 0; i < m->len; i++) {
            psd_str_decref(m->keys[i]);
            if (m->kind == PSD_ELEM_STR) psd_str_decref((psd_str *)(intptr_t)m->vals[i]);
            else if (m->kind == PSD_ELEM_LIST) psd_list_decref((psd_list *)(intptr_t)m->vals[i]);
            else if (m->kind == PSD_ELEM_MAP) psd_map_decref((psd_map *)(intptr_t)m->vals[i]);
            else if (m->kind == PSD_ELEM_JSON) psd_json_decref((psd_json *)(intptr_t)m->vals[i]);
        }
        free(m->keys);
        free(m->vals);
        free(m);
    }
}

psd_map *psd_map_new(int64_t elem_kind, int64_t hint_cap) {
    psd_map *m = (psd_map *)psd_alloc(sizeof(psd_map));
    m->rc = 1;
    m->len = 0;
    m->cap = hint_cap > 0 ? hint_cap : 4;
    m->kind = elem_kind;
    m->keys = (psd_str **)psd_alloc(m->cap * (int64_t)sizeof(psd_str *));
    m->vals = (int64_t *)psd_alloc(m->cap * (int64_t)sizeof(int64_t));
    return m;
}

int64_t psd_map_len(psd_map *m) { return m ? m->len : 0; }

static int64_t psd_map_find(psd_map *m, psd_str *key) {
    for (int64_t i = 0; i < m->len; i++)
        if (psd_str_eq(m->keys[i], key)) return i;
    return -1;
}

static void psd_map_slot_incref(psd_map *m, int64_t v) {
    if (m->kind == PSD_ELEM_STR) psd_str_incref((psd_str *)(intptr_t)v);
    else if (m->kind == PSD_ELEM_LIST) psd_list_incref((psd_list *)(intptr_t)v);
    else if (m->kind == PSD_ELEM_MAP) psd_map_incref((psd_map *)(intptr_t)v);
    else if (m->kind == PSD_ELEM_JSON) psd_json_incref((psd_json *)(intptr_t)v);
}

static void psd_map_slot_decref(psd_map *m, int64_t v) {
    if (m->kind == PSD_ELEM_STR) psd_str_decref((psd_str *)(intptr_t)v);
    else if (m->kind == PSD_ELEM_LIST) psd_list_decref((psd_list *)(intptr_t)v);
    else if (m->kind == PSD_ELEM_MAP) psd_map_decref((psd_map *)(intptr_t)v);
    else if (m->kind == PSD_ELEM_JSON) psd_json_decref((psd_json *)(intptr_t)v);
}

void psd_map_set(psd_map *m, psd_str *key, int64_t value) {
    if (!m) psd_panic("map is null");
    int64_t i = psd_map_find(m, key);
    if (i >= 0) {
        psd_map_slot_incref(m, value);
        psd_map_slot_decref(m, m->vals[i]);
        m->vals[i] = value;
        return;
    }
    if (m->len == m->cap) {
        m->cap *= 2;
        m->keys = (psd_str **)realloc(m->keys, (size_t)m->cap * sizeof(psd_str *));
        m->vals = (int64_t *)realloc(m->vals, (size_t)m->cap * sizeof(int64_t));
        if (!m->keys || !m->vals) psd_panic("out of memory");
    }
    psd_str_incref(key);
    psd_map_slot_incref(m, value);
    m->keys[m->len] = key;
    m->vals[m->len] = value;
    m->len++;
}

/* consume a temporary key (map_set increfs; caller drops its own ref) */
static void psd_map_set_k(psd_map *m, psd_str *key, int64_t value) {
    psd_map_set(m, key, value);
    psd_str_decref(key);
}

int64_t psd_map_get(psd_map *m, psd_str *key) {
    if (!m) psd_panic("map is null");
    int64_t i = psd_map_find(m, key);
    if (i < 0) {
        psd_str *msg = psd_str_concat(psd_str_new("map key not found: ", 19), key);
        psd_panic(msg->data);
    }
    return m->vals[i];
}

int64_t psd_map_has(psd_map *m, psd_str *key) {
    return m && key && psd_map_find(m, key) >= 0 ? 1 : 0;
}

void psd_map_remove(psd_map *m, psd_str *key) {
    if (!m || !key) return;
    int64_t i = psd_map_find(m, key);
    if (i < 0) return;
    psd_str_decref(m->keys[i]);
    psd_map_slot_decref(m, m->vals[i]);
    for (int64_t j = i; j < m->len - 1; j++) {
        m->keys[j] = m->keys[j + 1];
        m->vals[j] = m->vals[j + 1];
    }
    m->len--;
}

psd_list *psd_map_keys(psd_map *m) {
    psd_list *l = psd_list_new(PSD_ELEM_STR, m ? m->len : 0);
    for (int64_t i = 0; i < (m ? m->len : 0); i++)
        psd_list_push(l, (int64_t)(intptr_t)m->keys[i]);
    return l;
}

psd_list *psd_map_values(psd_map *m) {
    psd_list *l = psd_list_new(m ? m->kind : PSD_ELEM_SCALAR, m ? m->len : 0);
    for (int64_t i = 0; i < (m ? m->len : 0); i++)
        psd_list_push(l, m->vals[i]);
    return l;
}

/* ====================================================================== */
/* v0.2: dynamic json value                                               */
/* ====================================================================== */

enum { JSON_NULL = 0, JSON_BOOL = 1, JSON_INT = 2, JSON_FLOAT = 3,
       JSON_STR = 4, JSON_LIST = 5, JSON_MAP = 6 };

typedef struct psd_json {
    int64_t rc;
    int8_t kind;
    int64_t num;           /* int / bool / float bits */
    psd_str *s;            /* kind STR */
    psd_list *list;        /* kind LIST: slots hold psd_json* */
    psd_map *map;          /* kind MAP: values hold psd_json* */
} psd_json;

void psd_json_incref(psd_json *j) { if (j) j->rc++; }

void psd_json_decref(psd_json *j) {
    if (!j) return;
    if (--j->rc <= 0) {
        psd_str_decref(j->s);
        psd_list_decref(j->list);
        psd_map_decref(j->map);
        free(j);
    }
}

static psd_json *psd_json_alloc(int8_t kind) {
    psd_json *j = (psd_json *)psd_alloc(sizeof(psd_json));
    j->rc = 1;
    j->kind = kind;
    j->num = 0;
    j->s = NULL;
    j->list = NULL;
    j->map = NULL;
    return j;
}

psd_json *psd_json_null(void) { return psd_json_alloc(JSON_NULL); }
psd_json *psd_json_from_bool(int8_t v) {
    psd_json *j = psd_json_alloc(JSON_BOOL);
    j->num = v ? 1 : 0;
    return j;
}
psd_json *psd_json_from_int(int64_t v) {
    psd_json *j = psd_json_alloc(JSON_INT);
    j->num = v;
    return j;
}
psd_json *psd_json_from_float(double v) {
    psd_json *j = psd_json_alloc(JSON_FLOAT);
    memcpy(&j->num, &v, sizeof(double));
    return j;
}
psd_json *psd_json_from_str(psd_str *s) {
    psd_json *j = psd_json_alloc(JSON_STR);
    j->s = s;              /* consumes the reference */
    return j;
}

/* wrap any static value into a json (consumes the value's reference) */
psd_json *psd_json_from_list(psd_list *l);
psd_json *psd_json_from_map(psd_map *m);

psd_json *psd_json_wrap(int64_t slot, int64_t kind) {
    if (kind == PSD_ELEM_STR) return psd_json_from_str((psd_str *)(intptr_t)slot);
    if (kind == PSD_ELEM_LIST) return psd_json_from_list((psd_list *)(intptr_t)slot);
    if (kind == PSD_ELEM_MAP) return psd_json_from_map((psd_map *)(intptr_t)slot);
    if (kind == PSD_ELEM_JSON) { psd_json_incref((psd_json *)(intptr_t)slot); return (psd_json *)(intptr_t)slot; }
    if (kind == PSD_ELEM_BOOL) return psd_json_from_bool((int8_t)(slot & 1));
    if (kind == PSD_ELEM_FLOAT) {
        double d;
        memcpy(&d, &slot, sizeof(double));
        return psd_json_from_float(d);
    }
    return psd_json_from_int(slot);
}

psd_json *psd_json_from_list(psd_list *l) {
    /* a static list becomes a list of json values (elements are wrapped in place) */
    if (l && l->kind != PSD_ELEM_JSON) {
        for (int64_t i = 0; i < l->len; i++)
            l->data[i] = (int64_t)(intptr_t)psd_json_wrap(l->data[i], l->kind);
        l->kind = PSD_ELEM_JSON;
    }
    psd_json *j = psd_json_alloc(JSON_LIST);
    j->list = l;           /* consumes */
    return j;
}

psd_json *psd_json_from_map(psd_map *m) {
    if (m && m->kind != PSD_ELEM_JSON) {
        for (int64_t i = 0; i < m->len; i++)
            m->vals[i] = (int64_t)(intptr_t)psd_json_wrap(m->vals[i], m->kind);
        m->kind = PSD_ELEM_JSON;
    }
    psd_json *j = psd_json_alloc(JSON_MAP);
    j->map = m;            /* consumes */
    return j;
}

double psd_json_as_f64(psd_json *j) {
    double d;
    memcpy(&d, &j->num, sizeof(double));
    return d;
}

int64_t psd_json_len(psd_json *j) {
    if (!j) return 0;
    if (j->kind == JSON_STR) return j->s ? j->s->len : 0;
    if (j->kind == JSON_LIST) return j->list ? j->list->len : 0;
    if (j->kind == JSON_MAP) return j->map ? j->map->len : 0;
    psd_panic("len(): json value is not a string, array or object");
    return 0;
}

psd_json *psd_json_get_str(psd_json *j, psd_str *key) {
    if (!j || j->kind != JSON_MAP) psd_panic("cannot index this json value with a string key");
    int64_t v = psd_map_get(j->map, key);   /* panics if missing */
    psd_json *r = (psd_json *)(intptr_t)v;
    psd_json_incref(r);
    return r;
}

psd_json *psd_json_get_idx(psd_json *j, int64_t i) {
    if (!j) psd_panic("cannot index a null json value");
    if (j->kind == JSON_LIST) {
        int64_t v = psd_list_get(j->list, i);   /* bounds-checked */
        psd_json *r = (psd_json *)(intptr_t)v;
        psd_json_incref(r);
        return r;
    }
    if (j->kind == JSON_MAP) {
        if (!j->map || i < 0 || i >= j->map->len) psd_panic("index out of range");
        psd_json *r = (psd_json *)(intptr_t)j->map->vals[i];
        psd_json_incref(r);
        return r;
    }
    psd_panic("cannot index this json value with an integer");
    return NULL;
}

void psd_json_set_str(psd_json *j, psd_str *key, psd_json *v) {
    if (!j || j->kind != JSON_MAP) psd_panic("cannot assign with a string key on this json value");
    psd_map_set(j->map, key, (int64_t)(intptr_t)v);   /* increfs */
}

void psd_json_set_idx(psd_json *j, int64_t i, psd_json *v) {
    if (!j || j->kind != JSON_LIST) psd_panic("cannot assign with an integer index on this json value");
    if (!j->list || i < 0 || i >= j->list->len) psd_panic("index out of range");
    psd_list_set(j->list, i, (int64_t)(intptr_t)v);   /* increfs, decrefs old */
}

int64_t psd_json_has(psd_json *j, psd_str *key) {
    if (!j || j->kind != JSON_MAP) return 0;
    return psd_map_has(j->map, key);
}

void psd_json_push(psd_json *j, psd_json *v) {
    if (!j || j->kind != JSON_LIST) psd_panic("push(): json value is not an array");
    if (!j->list) j->list = psd_list_new(PSD_ELEM_JSON, 4);
    psd_list_push(j->list, (int64_t)(intptr_t)v);   /* increfs */
}

psd_list *psd_json_keys(psd_json *j) {
    if (!j || j->kind != JSON_MAP) psd_panic("keys(): json value is not an object");
    return psd_map_keys(j->map);
}

/* conversions (dynamic unboxing) --------------------------------------- */

int64_t psd_json_as_int(psd_json *j) {
    if (!j) psd_panic("int(): json value is null");
    if (j->kind == JSON_INT) return j->num;
    if (j->kind == JSON_BOOL) return j->num;
    if (j->kind == JSON_FLOAT) return (int64_t)psd_json_as_f64(j);
    if (j->kind == JSON_STR) return psd_str_to_i64(j->s);
    psd_panic("int(): json value is not a number or string");
    return 0;
}

double psd_json_as_float(psd_json *j) {
    if (!j) psd_panic("float(): json value is null");
    if (j->kind == JSON_FLOAT) return psd_json_as_f64(j);
    if (j->kind == JSON_INT || j->kind == JSON_BOOL) return (double)j->num;
    if (j->kind == JSON_STR) return psd_str_to_f64(j->s);
    psd_panic("float(): json value is not a number or string");
    return 0;
}

int8_t psd_json_as_bool(psd_json *j) {
    if (!j) psd_panic("bool conversion: json value is null");
    if (j->kind == JSON_BOOL) return (int8_t)j->num;
    if (j->kind == JSON_INT) return j->num != 0;
    psd_panic("bool conversion: json value is not a bool");
    return 0;
}

void psd_str_decref(psd_str *s);

psd_str *psd_json_as_str(psd_json *j) {
    if (!j) psd_panic("str(): json value is null");
    if (j->kind == JSON_STR) { psd_str_incref(j->s); return j->s; }
    psd_panic("str(): json value is not a string (use json_encode)");
    return NULL;
}

/* encode --------------------------------------------------------------- */

static void psd_enc_str(const psd_str *s, char **buf, int64_t *n, int64_t *cap) {
    void psd_enc_push(char c, char **buf, int64_t *n, int64_t *cap);
    void psd_enc_raw(const char *p, int64_t len, char **buf, int64_t *n, int64_t *cap);
    psd_enc_push('"', buf, n, cap);
    for (int64_t i = 0; i < s->len; i++) {
        unsigned char c = (unsigned char)s->data[i];
        if (c == '"' || c == '\\') {
            char esc[2] = {'\\', (char)c};
            psd_enc_raw(esc, 2, buf, n, cap);
        } else if (c == '\n') psd_enc_raw("\\n", 2, buf, n, cap);
        else if (c == '\t') psd_enc_raw("\\t", 2, buf, n, cap);
        else if (c == '\r') psd_enc_raw("\\r", 2, buf, n, cap);
        else if (c < 0x20) {
            char tmp[8];
            snprintf(tmp, sizeof tmp, "\\u%04x", c);
            psd_enc_raw(tmp, 6, buf, n, cap);
        } else psd_enc_push((char)c, buf, n, cap);
    }
    psd_enc_push('"', buf, n, cap);
}

void psd_enc_push(char c, char **buf, int64_t *n, int64_t *cap) {
    if (*n + 2 > *cap) {
        *cap *= 2;
        *buf = (char *)realloc(*buf, (size_t)*cap);
        if (!*buf) psd_panic("out of memory");
    }
    (*buf)[(*n)++] = c;
}

void psd_enc_raw(const char *p, int64_t len, char **buf, int64_t *n, int64_t *cap) {
    while (*n + len + 1 > *cap) {
        *cap *= 2;
        *buf = (char *)realloc(*buf, (size_t)*cap);
        if (!*buf) psd_panic("out of memory");
    }
    memcpy(*buf + *n, p, (size_t)len);
    *n += len;
}

static void psd_json_encode_into(psd_json *j, char **buf, int64_t *n, int64_t *cap) {
    void psd_enc_push(char c, char **buf, int64_t *n, int64_t *cap);
    void psd_enc_raw(const char *p, int64_t len, char **buf, int64_t *n, int64_t *cap);
    if (!j) { psd_enc_raw("null", 4, buf, n, cap); return; }
    switch (j->kind) {
    case JSON_NULL: psd_enc_raw("null", 4, buf, n, cap); break;
    case JSON_BOOL: psd_enc_raw(j->num ? "true" : "false", j->num ? 4 : 5, buf, n, cap); break;
    case JSON_INT: {
        char tmp[32];
        int k = snprintf(tmp, sizeof tmp, "%lld", (long long)j->num);
        psd_enc_raw(tmp, k, buf, n, cap);
        break;
    }
    case JSON_FLOAT: {
        char tmp[40];
        psd_fmt_double(psd_json_as_f64(j), tmp, sizeof tmp);
        psd_enc_raw(tmp, (int64_t)strlen(tmp), buf, n, cap);
        break;
    }
    case JSON_STR: psd_enc_str(j->s ? j->s : psd_str_new("", 0), buf, n, cap); break;
    case JSON_LIST: {
        psd_enc_push('[', buf, n, cap);
        for (int64_t i = 0; i < (j->list ? j->list->len : 0); i++) {
            if (i) psd_enc_push(',', buf, n, cap);
            psd_json_encode_into((psd_json *)(intptr_t)j->list->data[i], buf, n, cap);
        }
        psd_enc_push(']', buf, n, cap);
        break;
    }
    case JSON_MAP: {
        psd_enc_push('{', buf, n, cap);
        for (int64_t i = 0; i < (j->map ? j->map->len : 0); i++) {
            if (i) psd_enc_push(',', buf, n, cap);
            psd_enc_str(j->map->keys[i], buf, n, cap);
            psd_enc_push(':', buf, n, cap);
            psd_json_encode_into((psd_json *)(intptr_t)j->map->vals[i], buf, n, cap);
        }
        psd_enc_push('}', buf, n, cap);
        break;
    }
    }
}

psd_str *psd_json_encode(psd_json *j) {
    int64_t cap = 64, n = 0;
    char *buf = (char *)psd_alloc(cap);
    psd_json_encode_into(j, &buf, &n, &cap);
    psd_str *r = psd_str_new(buf, n);
    free(buf);
    return r;
}

/* display form: raw string for str values, JSON encoding otherwise */
psd_str *psd_json_display(psd_json *j) {
    if (!j) return psd_str_new("null", 4);
    if (j->kind == JSON_STR) {
        psd_str_incref(j->s);
        return j->s;
    }
    return psd_json_encode(j);
}

/* structural equality (int/float compare numerically) */
int64_t psd_json_eq(psd_json *a, psd_json *b) {
    if (a == b) return 1;
    if (!a || !b) return 0;
    if ((a->kind == JSON_INT || a->kind == JSON_FLOAT || a->kind == JSON_BOOL) &&
        (b->kind == JSON_INT || b->kind == JSON_FLOAT || b->kind == JSON_BOOL)) {
        double x = a->kind == JSON_FLOAT ? psd_json_as_f64(a) : (double)a->num;
        double y = b->kind == JSON_FLOAT ? psd_json_as_f64(b) : (double)b->num;
        return x == y ? 1 : 0;
    }
    if (a->kind != b->kind) return 0;
    switch (a->kind) {
    case JSON_NULL: return 1;
    case JSON_STR: return psd_str_eq(a->s, b->s);
    case JSON_LIST: {
        int64_t n = a->list ? a->list->len : 0;
        int64_t m = b->list ? b->list->len : 0;
        if (n != m) return 0;
        for (int64_t i = 0; i < n; i++)
            if (!psd_json_eq((psd_json *)(intptr_t)a->list->data[i],
                             (psd_json *)(intptr_t)b->list->data[i])) return 0;
        return 1;
    }
    case JSON_MAP: {
        if ((a->map ? a->map->len : 0) != (b->map ? b->map->len : 0)) return 0;
        for (int64_t i = 0; i < (a->map ? a->map->len : 0); i++) {
            psd_str *k = a->map->keys[i];
            if (!psd_map_has(b->map, k)) return 0;
            if (!psd_json_eq((psd_json *)(intptr_t)a->map->vals[i],
                             (psd_json *)(intptr_t)psd_map_get(b->map, k))) return 0;
        }
        return 1;
    }
    }
    return 0;
}

/* Python-like truthiness for json conditions */
int8_t psd_json_truthy(psd_json *j) {
    if (!j) return 0;
    switch (j->kind) {
    case JSON_NULL: return 0;
    case JSON_BOOL: return (int8_t)j->num;
    case JSON_INT: return j->num != 0;
    case JSON_FLOAT: return psd_json_as_f64(j) != 0.0;
    case JSON_STR: return j->s && j->s->len > 0;
    case JSON_LIST: return j->list && j->list->len > 0;
    case JSON_MAP: return j->map && j->map->len > 0;
    }
    return 0;
}

void psd_print_json(psd_json *j) {
    if (j && j->kind == JSON_STR && j->s) {
        psd_print_str(j->s);
        return;
    }
    psd_str *s = psd_json_encode(j);
    psd_print_str(s);
    psd_str_decref(s);
}

/* parse ---------------------------------------------------------------- */

typedef struct {
    const char *p;
    const char *end;
} psd_jp;

static void psd_jp_ws(psd_jp *jp) {
    while (jp->p < jp->end && (*jp->p == ' ' || *jp->p == '\t' || *jp->p == '\n' || *jp->p == '\r'))
        jp->p++;
}

static void psd_jp_utf8(char *out, int64_t *n, unsigned long cp) {
    if (cp < 0x80) out[(*n)++] = (char)cp;
    else if (cp < 0x800) {
        out[(*n)++] = (char)(0xC0 | (cp >> 6));
        out[(*n)++] = (char)(0x80 | (cp & 0x3F));
    } else {
        out[(*n)++] = (char)(0xE0 | (cp >> 12));
        out[(*n)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[(*n)++] = (char)(0x80 | (cp & 0x3F));
    }
}

static psd_json *psd_jp_value(psd_jp *jp);

static psd_str *psd_jp_string(psd_jp *jp) {
    if (*jp->p != '"') psd_panic("json_parse: expected a string");
    jp->p++;
    int64_t cap = 32, n = 0;
    char *buf = (char *)psd_alloc(cap);
    while (jp->p < jp->end && *jp->p != '"') {
        char c = *jp->p++;
        if (c == '\\') {
            if (jp->p >= jp->end) psd_panic("json_parse: bad escape");
            char e = *jp->p++;
            switch (e) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case '/': c = '/'; break;
            case '"': c = '"'; break;
            case '\\': c = '\\'; break;
            case 'u': {
                if (jp->p + 4 > jp->end) psd_panic("json_parse: bad \\u escape");
                char hx[5] = {jp->p[0], jp->p[1], jp->p[2], jp->p[3], 0};
                jp->p += 4;
                unsigned long cp = strtoul(hx, NULL, 16);
                char tmp[8];
                int64_t tn = 0;
                psd_jp_utf8(tmp, &tn, cp);
                while (n + tn + 1 > cap) { cap *= 2; buf = (char *)realloc(buf, (size_t)cap); if (!buf) psd_panic("out of memory"); }
                memcpy(buf + n, tmp, (size_t)tn);
                n += tn;
                continue;
            }
            default: psd_panic("json_parse: bad escape");
            }
        }
        if (n + 2 > cap) { cap *= 2; buf = (char *)realloc(buf, (size_t)cap); if (!buf) psd_panic("out of memory"); }
        buf[n++] = c;
    }
    if (jp->p >= jp->end) psd_panic("json_parse: unterminated string");
    jp->p++;  /* closing quote */
    psd_str *s = psd_str_new(buf, n);
    free(buf);
    return s;
}

static psd_json *psd_jp_value(psd_jp *jp) {
    psd_jp_ws(jp);
    if (jp->p >= jp->end) psd_panic("json_parse: unexpected end of input");
    char c = *jp->p;
    if (c == 'n') {
        if (jp->p + 4 <= jp->end && memcmp(jp->p, "null", 4) == 0) {
            jp->p += 4;
            return psd_json_null();
        }
        psd_panic("json_parse: invalid literal");
    }
    if (c == 't') {
        if (jp->p + 4 <= jp->end && memcmp(jp->p, "true", 4) == 0) {
            jp->p += 4;
            return psd_json_from_bool(1);
        }
        psd_panic("json_parse: invalid literal");
    }
    if (c == 'f') {
        if (jp->p + 5 <= jp->end && memcmp(jp->p, "false", 5) == 0) {
            jp->p += 5;
            return psd_json_from_bool(0);
        }
        psd_panic("json_parse: invalid literal");
    }
    if (c == '"') return psd_json_from_str(psd_jp_string(jp));
    if (c == '[') {
        jp->p++;
        psd_list *l = psd_list_new(PSD_ELEM_JSON, 4);
        psd_jp_ws(jp);
        if (jp->p < jp->end && *jp->p == ']') {
            jp->p++;
            return psd_json_from_list(l);
        }
        while (1) {
            psd_json *v = psd_jp_value(jp);
            psd_list_push(l, (int64_t)(intptr_t)v);
            psd_json_decref(v);   /* push increfs */
            psd_jp_ws(jp);
            if (jp->p < jp->end && *jp->p == ',') { jp->p++; continue; }
            if (jp->p < jp->end && *jp->p == ']') { jp->p++; break; }
            psd_panic("json_parse: expected ',' or ']'");
        }
        return psd_json_from_list(l);
    }
    if (c == '{') {
        jp->p++;
        psd_map *m = psd_map_new(PSD_ELEM_JSON, 4);
        psd_jp_ws(jp);
        if (jp->p < jp->end && *jp->p == '}') {
            jp->p++;
            return psd_json_from_map(m);
        }
        while (1) {
            psd_jp_ws(jp);
            psd_str *k = psd_jp_string(jp);
            psd_jp_ws(jp);
            if (jp->p >= jp->end || *jp->p != ':') psd_panic("json_parse: expected ':'");
            jp->p++;
            psd_json *v = psd_jp_value(jp);
            psd_map_set(m, k, (int64_t)(intptr_t)v);
            psd_str_decref(k);
            psd_json_decref(v);
            psd_jp_ws(jp);
            if (jp->p < jp->end && *jp->p == ',') { jp->p++; continue; }
            if (jp->p < jp->end && *jp->p == '}') { jp->p++; break; }
            psd_panic("json_parse: expected ',' or '}'");
        }
        return psd_json_from_map(m);
    }
    /* number */
    {
        char *endptr = NULL;
        double d = strtod(jp->p, &endptr);
        if (endptr == jp->p) psd_panic("json_parse: invalid value");
        int is_float = 0;
        for (const char *q = jp->p; q < endptr; q++)
            if (*q == '.' || *q == 'e' || *q == 'E') is_float = 1;
        jp->p = endptr;
        if (is_float) return psd_json_from_float(d);
        return psd_json_from_int((int64_t)d);
    }
}

psd_json *psd_json_parse(psd_str *s) {
    psd_jp jp = {s ? s->data : "", (s ? s->data : "") + (s ? s->len : 0)};
    psd_json *j = psd_jp_value(&jp);
    psd_jp_ws(&jp);
    if (jp.p != jp.end) psd_panic("json_parse: trailing characters");
    return j;
}

/* ====================================================================== */
/* v0.2: files, shell, http, process                                      */
/* ====================================================================== */

/* ---- capability gates -------------------------------------------------
 * PSD_DISABLE_PROCESS=1  blocks run / capture / shell / capture_shell
 * PSD_DISABLE_HTTP=1     blocks http_get / http_post / http_request
 * PSD_DISABLE_FILES=1    blocks read_file / write_file / append_file /
 *                         delete / list_dir / exists
 * Use these when embedding PSD programs in restricted contexts.
 */

static void psd_cap_check(const char *env_name, const char *what) {
    const char *v = getenv(env_name);
    if (v && v[0] == '1') {
        char msg[256];
        snprintf(msg, sizeof msg, "%s is disabled (unset %s to allow)", what, env_name);
        psd_panic(msg);
    }
}

psd_str *psd_read_file(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    FILE *f = fopen(path->data, "rb");
    if (!f) {
        psd_str *msg = psd_str_concat(psd_str_new("cannot read file: ", 18), path);
        psd_panic(msg->data);
    }
    int64_t cap = 4096, n = 0;
    char *buf = (char *)psd_alloc(cap);
    size_t r;
    while ((r = fread(buf + n, 1, (size_t)(cap - n), f)) > 0) {
        n += (int64_t)r;
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
    }
    fclose(f);
    psd_str *s = psd_str_new(buf, n);
    free(buf);
    return s;
}

void psd_write_file(psd_str *path, psd_str *contents) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    FILE *f = fopen(path->data, "wb");
    if (!f) {
        psd_str *msg = psd_str_concat(psd_str_new("cannot write file: ", 19), path);
        psd_panic(msg->data);
    }
    if (contents && contents->len > 0)
        fwrite(contents->data, 1, (size_t)contents->len, f);
    fclose(f);
}

void psd_append_file(psd_str *path, psd_str *contents) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    FILE *f = fopen(path->data, "ab");
    if (!f) {
        psd_str *msg = psd_str_concat(psd_str_new("cannot append to file: ", 23), path);
        psd_panic(msg->data);
    }
    if (contents && contents->len > 0)
        fwrite(contents->data, 1, (size_t)contents->len, f);
    fclose(f);
}

int64_t psd_exists(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    FILE *f = fopen(path->data, "rb");
    if (f) { fclose(f); return 1; }
    return 0;
}

void psd_delete(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    if (remove(path->data) != 0) {
        psd_str *msg = psd_str_concat(psd_str_new("cannot delete: ", 15), path);
        psd_panic(msg->data);
    }
}

#include <dirent.h>

psd_list *psd_list_dir(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    DIR *d = opendir(path->data);
    if (!d) {
        psd_str *msg = psd_str_concat(psd_str_new("cannot open directory: ", 23), path);
        psd_panic(msg->data);
    }
    psd_list *out = psd_list_new(PSD_ELEM_STR, 8);
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) continue;
        psd_str *s = psd_str_new(e->d_name, (int64_t)strlen(e->d_name));
        psd_list_push(out, (int64_t)(intptr_t)s);
        psd_str_decref(s);
    }
    closedir(d);
    return out;
}

/* ---- process execution ------------------------------------------------
 * Two APIs, one trust boundary:
 *   run([prog, arg...]) / capture([prog, arg...])
 *       argv-based (fork + execvp) -- NO shell, safe with untrusted values.
 *   shell(cmd) / capture_shell(cmd)
 *       the string goes to the system shell (system/popen). Metacharacters
 *       ARE interpreted. Only use with fully trusted strings.
 */

static char **psd_argv_array(psd_list *argv_list) {
    if (!argv_list || argv_list->kind != PSD_ELEM_STR)
        psd_panic("command must be a [str] list of program + arguments");
    if (argv_list->len == 0)
        psd_panic("command list is empty");
    int64_t n = argv_list->len;
    char **av = (char **)psd_alloc((n + 1) * (int64_t)sizeof(char *));
    for (int64_t i = 0; i < n; i++) {
        psd_str *s = (psd_str *)(intptr_t)argv_list->data[i];
        av[i] = s ? s->data : (char *)"";
    }
    av[n] = NULL;
    return av;
}

static int psd_wait_code(int status) {
#ifdef WIFEXITED
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 1;
#else
    return (status >> 8) & 0xFF;
#endif
}

int64_t psd_run(psd_list *argv_list) {
    psd_cap_check("PSD_DISABLE_PROCESS", "process execution");
    char **av = psd_argv_array(argv_list);
    fflush(stdout);
    pid_t pid = fork();
    if (pid < 0) psd_panic("run(): fork failed");
    if (pid == 0) {
        execvp(av[0], av);
        _exit(127);          /* program not found */
    }
    int status = 0;
    waitpid(pid, &status, 0);
    free(av);
    return psd_wait_code(status);
}

psd_str *psd_capture(psd_list *argv_list) {
    psd_cap_check("PSD_DISABLE_PROCESS", "process execution");
    char **av = psd_argv_array(argv_list);
    int fds[2];
    if (pipe(fds) != 0) psd_panic("capture(): pipe failed");
    pid_t pid = fork();
    if (pid < 0) psd_panic("capture(): fork failed");
    if (pid == 0) {
        close(fds[0]);
        dup2(fds[1], 1);     /* capture stdout only... */
        /* ...stderr passes through to the console so failures stay visible */
        close(fds[1]);
        execvp(av[0], av);
        _exit(127);
    }
    close(fds[1]);
    int64_t cap = 4096, n = 0;
    char *buf = (char *)psd_alloc(cap);
    ssize_t r;
    while ((r = read(fds[0], buf + n, (size_t)(cap - n))) > 0) {
        n += r;
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
    }
    close(fds[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    free(av);
    psd_str *out = psd_str_new(buf, n);
    free(buf);
    int code = psd_wait_code(status);
    if (code != 0) {
        fprintf(stderr, "psd: runtime error: command failed (exit %d)\n",
                code);
        exit(1);
    }
    if (out->len > 0 && out->data[out->len - 1] == '\n')
        out->data[--out->len] = '\0';
    return out;
}

int64_t psd_shell(psd_str *cmd) {
    psd_cap_check("PSD_DISABLE_PROCESS", "process execution");
    fflush(stdout);
    int status = system(cmd->data);
    if (status == -1) psd_panic("shell(): failed to run command");
    return psd_wait_code(status);
}

psd_str *psd_capture_shell(psd_str *cmd) {
    psd_cap_check("PSD_DISABLE_PROCESS", "process execution");
    /* merge stderr so failures are debuggable */
    psd_str *suffix = psd_str_new(" 2>&1", 5);
    psd_str *full = psd_str_concat(cmd, suffix);
    FILE *p = popen(full->data, "r");
    psd_str_decref(full);
    psd_str_decref(suffix);
    if (!p) psd_panic("capture_shell(): failed to run command");
    int64_t cap = 4096, n = 0;
    char *buf = (char *)psd_alloc(cap);
    size_t r;
    while ((r = fread(buf + n, 1, (size_t)(cap - n), p)) > 0) {
        n += (int64_t)r;
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
    }
    int status = pclose(p);
    psd_str *out = psd_str_new(buf, n);
    free(buf);
    int code = psd_wait_code(status);
    if (code != 0) {
        fprintf(stderr, "psd: runtime error: command failed (exit %d)\n",
                code);
        exit(1);
    }
    if (out->len > 0 && out->data[out->len - 1] == '\n')
        out->data[--out->len] = '\0';
    return out;
}

/* http via curl --------------------------------------------------------- */

static void psd_shell_quote(const char *s, char *out, int64_t cap) {
    int64_t n = 0;
    if (n + 1 >= cap) psd_panic("http: argument too long");
    out[n++] = '\'';
    for (const char *p = s; *p; p++) {
        if (*p == '\'') {
            if (n + 5 >= cap) psd_panic("http: argument too long");
            memcpy(out + n, "'\\''", 4);
            n += 4;
        } else {
            if (n + 2 >= cap) psd_panic("http: argument too long");
            out[n++] = *p;
        }
    }
    if (n + 2 >= cap) psd_panic("http: argument too long");
    out[n++] = '\'';
    out[n] = 0;
}

static int psd_http_quiet = 0;   /* set by psd_try_http_get */

psd_json *psd_http_request(psd_str *method, psd_str *url, psd_str *body, psd_map *headers) {
    psd_cap_check("PSD_DISABLE_HTTP", "http access");
    char cmd[65536];
    char q[32768];
    snprintf(cmd, sizeof cmd, "curl -sS --max-time 120 -X ");
    psd_shell_quote(method->data, q, sizeof q);
    strncat(cmd, q, sizeof cmd - strlen(cmd) - 1);
    strncat(cmd, " -w '\\n%{http_code}'", sizeof cmd - strlen(cmd) - 1);
    for (int64_t i = 0; i < (headers ? headers->len : 0); i++) {
        psd_str *k = headers->keys[i];
        psd_str *v = (psd_str *)(intptr_t)headers->vals[i];
        char hdr[4096], qh[4200];
        snprintf(hdr, sizeof hdr, "%s: %s", k->data, v->data);
        psd_shell_quote(hdr, qh, sizeof qh);
        strncat(cmd, " -H ", sizeof cmd - strlen(cmd) - 1);
        strncat(cmd, qh, sizeof cmd - strlen(cmd) - 1);
    }
    if (body && body->len > 0) {
        char qb[32768];
        psd_shell_quote(body->data, qb, sizeof qb);
        strncat(cmd, " -d ", sizeof cmd - strlen(cmd) - 1);
        strncat(cmd, qb, sizeof cmd - strlen(cmd) - 1);
    }
    strncat(cmd, " ", sizeof cmd - strlen(cmd) - 1);
    psd_shell_quote(url->data, q, sizeof q);
    strncat(cmd, q, sizeof cmd - strlen(cmd) - 1);

    FILE *p = popen(cmd, "r");
    if (!p) psd_panic("http_request(): failed to run curl");
    int64_t cap = 8192, n = 0;
    char *buf = (char *)psd_alloc(cap);
    size_t r;
    while ((r = fread(buf + n, 1, (size_t)(cap - n), p)) > 0) {
        n += (int64_t)r;
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
    }
    int status = pclose(p);
    (void)status;

    /* last line is the http status code */
    int64_t cut = n;
    while (cut > 0 && buf[cut - 1] != '\n') cut--;
    int64_t code = 0;
    if (cut > 0) code = strtoll(buf + cut, NULL, 10);
    if (code == 0) {
        if (psd_http_quiet) {
            free(buf);
            psd_map *m0 = psd_map_new(PSD_ELEM_JSON, 3);
            psd_json *z = psd_json_from_int(0);
            psd_map_set_k(m0, psd_str_new("status", 6), (int64_t)(intptr_t)z); psd_json_decref(z);
            z = psd_json_from_bool(0);
            psd_map_set_k(m0, psd_str_new("ok", 2), (int64_t)(intptr_t)z); psd_json_decref(z);
            z = psd_json_from_str(psd_str_new("", 0));
            psd_map_set_k(m0, psd_str_new("body", 4), (int64_t)(intptr_t)z); psd_json_decref(z);
            return psd_json_from_map(m0);
        }
        /* curl produced no HTTP status at all (host unreachable, DNS
         * failure, TLS refused). That is an operational fact, not a
         * programming error — report it as data instead of panicing so
         * automations and servers keep running. */
        psd_map *m0 = psd_map_new(PSD_ELEM_JSON, 3);
        psd_json *z = psd_json_from_int(0);
        psd_map_set_k(m0, psd_str_new("status", 6), (int64_t)(intptr_t)z); psd_json_decref(z);
        z = psd_json_from_bool(0);
        psd_map_set_k(m0, psd_str_new("ok", 2), (int64_t)(intptr_t)z); psd_json_decref(z);
        z = psd_json_from_str(psd_str_new("", 0));
        psd_map_set_k(m0, psd_str_new("body", 4), (int64_t)(intptr_t)z); psd_json_decref(z);
        return psd_json_from_map(m0);
    }
    int64_t body_len = cut > 0 ? cut - 1 : 0;

    psd_map *m = psd_map_new(PSD_ELEM_JSON, 4);
    psd_json *status_j = psd_json_from_int(code);
    psd_map_set_k(m, psd_str_new("status", 6), (int64_t)(intptr_t)status_j);
    psd_json_decref(status_j);
    psd_json *ok_j = psd_json_from_bool(code >= 200 && code < 300);
    psd_map_set_k(m, psd_str_new("ok", 2), (int64_t)(intptr_t)ok_j);
    psd_json_decref(ok_j);
    psd_json *body_j = psd_json_from_str(psd_str_new(buf, body_len));
    psd_map_set_k(m, psd_str_new("body", 4), (int64_t)(intptr_t)body_j);
    psd_json_decref(body_j);
    free(buf);
    return psd_json_from_map(m);
}

static int64_t psd_http_status_of(psd_json *r, psd_str **body_out) {
    psd_str *k_status = psd_str_new("status", 6);
    psd_json *status_j = psd_json_get_str(r, k_status);
    int64_t code = psd_json_as_int(status_j);
    psd_json_decref(status_j);
    psd_str_decref(k_status);
    psd_str *k_body = psd_str_new("body", 4);
    psd_json *body_j = psd_json_get_str(r, k_body);
    psd_str_decref(k_body);
    *body_out = psd_json_as_str(body_j);   /* owned */
    psd_json_decref(body_j);
    psd_json_decref(r);
    return code;
}

psd_str *psd_http_get(psd_str *url) {
    psd_str *method = psd_str_new("GET", 3);
    psd_json *r = psd_http_request(method, url, NULL, NULL);
    psd_str_decref(method);
    psd_str *body = NULL;
    int64_t code = psd_http_status_of(r, &body);   /* consumes r */
    if (code < 200 || code >= 300) {
        fprintf(stderr, "psd: runtime error: http_get failed (status %lld):\n%s\n",
                (long long)code, body->data);
        exit(1);
    }
    return body;
}

psd_str *psd_http_post(psd_str *url, psd_str *body, psd_str *content_type) {
    psd_map *h = psd_map_new(PSD_ELEM_STR, 2);
    psd_map_set_k(h, psd_str_new("Content-Type", 12), (int64_t)(intptr_t)content_type);
    psd_str *method = psd_str_new("POST", 4);
    psd_json *r = psd_http_request(method, url, body, h);
    psd_str_decref(method);
    psd_map_decref(h);
    psd_str *resp = NULL;
    int64_t code = psd_http_status_of(r, &resp);   /* consumes r */
    if (code < 200 || code >= 300) {
        fprintf(stderr, "psd: runtime error: http_post failed (status %lld):\n%s\n",
                (long long)code, resp->data);
        exit(1);
    }
    return resp;
}

/* recoverable errors (try_*) --------------------------------------------
 *
 * Every try_* builtin returns a json result envelope:
 *   {"ok": true,  "value": ...}
 *   {"ok": false, "error": "..."}            (+ extra keys where useful)
 * The panicking builtins are unchanged; try_* never terminates the program
 * for ordinary failures (missing file, bad input, non-zero exit, timeout).
 */

static psd_json *psd_try_ok(psd_json *value) {
    psd_map *m = psd_map_new(PSD_ELEM_JSON, 2);
    psd_json *ok_j = psd_json_from_bool(1);
    psd_map_set_k(m, psd_str_new("ok", 2), (int64_t)(intptr_t)ok_j);
    psd_json_decref(ok_j);
    psd_map_set_k(m, psd_str_new("value", 5), (int64_t)(intptr_t)value);
    psd_json_decref(value);
    return psd_json_from_map(m);
}

static psd_json *psd_try_err(const char *msg) {
    psd_map *m = psd_map_new(PSD_ELEM_JSON, 2);
    psd_json *ok_j = psd_json_from_bool(0);
    psd_map_set_k(m, psd_str_new("ok", 2), (int64_t)(intptr_t)ok_j);
    psd_json_decref(ok_j);
    psd_json *e_j = psd_json_from_str(psd_str_new(msg, (int64_t)strlen(msg)));
    psd_map_set_k(m, psd_str_new("error", 5), (int64_t)(intptr_t)e_j);
    psd_json_decref(e_j);
    return psd_json_from_map(m);
}

static psd_json *psd_try_err2(const char *msg, const char *key2, psd_json *v2,
                              const char *key3, psd_json *v3) {
    psd_map *m = psd_map_new(PSD_ELEM_JSON, 4);
    psd_json *ok_j = psd_json_from_bool(0);
    psd_map_set_k(m, psd_str_new("ok", 2), (int64_t)(intptr_t)ok_j);
    psd_json_decref(ok_j);
    psd_json *e_j = psd_json_from_str(psd_str_new(msg, (int64_t)strlen(msg)));
    psd_map_set_k(m, psd_str_new("error", 5), (int64_t)(intptr_t)e_j);
    psd_json_decref(e_j);
    if (v2) {
        psd_map_set_k(m, psd_str_new(key2, (int64_t)strlen(key2)), (int64_t)(intptr_t)v2);
        psd_json_decref(v2);
    }
    if (v3) {
        psd_map_set_k(m, psd_str_new(key3, (int64_t)strlen(key3)), (int64_t)(intptr_t)v3);
        psd_json_decref(v3);
    }
    return psd_json_from_map(m);
}

psd_json *psd_try_read_file(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    FILE *f = fopen(path->data, "rb");
    if (!f) return psd_try_err(strerror(errno));
    int64_t cap = 8192, n = 0;
    char *buf = (char *)psd_alloc(cap);
    size_t r;
    while ((r = fread(buf + n, 1, (size_t)(cap - n), f)) > 0) {
        n += (int64_t)r;
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
    }
    fclose(f);
    psd_json *v = psd_json_from_str(psd_str_new(buf, n));
    free(buf);
    return psd_try_ok(v);
}

psd_json *psd_try_write_file(psd_str *path, psd_str *text) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    FILE *f = fopen(path->data, "wb");
    if (!f) return psd_try_err(strerror(errno));
    size_t want = (size_t)text->len;
    size_t got = fwrite(text->data, 1, want, f);
    int bad = ferror(f);
    fclose(f);
    if (bad || got != want) return psd_try_err("short write");
    return psd_try_ok(psd_json_from_bool(1));
}

psd_json *psd_try_int(psd_str *s) {
    if (!s || s->len == 0) return psd_try_err("empty string");
    char *end = NULL;
    errno = 0;
    long long v = strtoll(s->data, &end, 10);
    if (end != s->data + s->len) return psd_try_err("not an integer");
    if (errno == ERANGE) return psd_try_err("out of int range");
    return psd_try_ok(psd_json_from_int((int64_t)v));
}

psd_json *psd_try_float(psd_str *s) {
    if (!s || s->len == 0) return psd_try_err("empty string");
    char *end = NULL;
    errno = 0;
    double v = strtod(s->data, &end);
    if (end != s->data + s->len) return psd_try_err("not a float");
    if (errno == ERANGE) return psd_try_err("out of float range");
    return psd_try_ok(psd_json_from_float(v));
}

static int psd_mkdir_p(const char *path);

psd_json *psd_try_make_dir(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    if (psd_mkdir_p(path->data) != 0)
        return psd_try_err(strerror(errno));
    return psd_try_ok(psd_json_from_bool(1));
}

psd_json *psd_try_json_parse(psd_str *s) {
    /* The main parser panics on invalid input. Validate in a child process
     * first (same code path, so the verdict always matches); only parse in
     * the parent when the child survived. */
    fflush(NULL);   /* child must not inherit (and later flush) our stdout */
    pid_t pid = fork();
    if (pid < 0) psd_panic("try_json_parse(): fork failed");
    if (pid == 0) {
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) { dup2(devnull, 2); if (devnull != 2) close(devnull); }
        psd_json *j = psd_json_parse(s);
        psd_json_decref(j);
        _exit(0);
    }
    int st = 0;
    waitpid(pid, &st, 0);
    if (psd_wait_code(st) != 0) return psd_try_err("invalid json");
    return psd_try_ok(psd_json_parse(s));
}

psd_json *psd_try_http_get(psd_str *url) {
    psd_http_quiet = 1;
    psd_str *method = psd_str_new("GET", 3);
    psd_json *r = psd_http_request(method, url, NULL, NULL);
    psd_str_decref(method);
    psd_http_quiet = 0;
    psd_str *body = NULL;
    int64_t code = psd_http_status_of(r, &body);   /* consumes r */
    if (code == 0) {
        psd_str_decref(body);
        return psd_try_err("no response (is curl installed? is the URL correct?)");
    }
    if (code < 200 || code >= 300) {
        psd_json *st_j = psd_json_from_int(code);
        psd_json *b_j = psd_json_from_str(body);   /* json_from_str consumes body */
        return psd_try_err2("http status not ok", "status", st_j, "value", b_j);
    }
    psd_json *v = psd_json_from_str(body);          /* consumes body */
    return psd_try_ok(v);
}

/* process capture with optional timeout (<=0 = no timeout).
 * Returns the same envelope shape; "code" carries the exit code (or -1 on
 * timeout) and "value" the captured stdout (possibly partial). */
static psd_json *psd_capture_core(char **av, const char *shellcmd,
                                  int64_t timeout_sec) {
    psd_cap_check("PSD_DISABLE_PROCESS", "process execution");
    int fds[2];
    if (pipe(fds) != 0) return psd_try_err("pipe failed");
    pid_t pid = fork();
    if (pid < 0) return psd_try_err("fork failed");
    if (pid == 0) {
        close(fds[0]);
        dup2(fds[1], 1);
        close(fds[1]);
        if (shellcmd) execl("/bin/sh", "sh", "-c", shellcmd, (char *)NULL);
        else execvp(av[0], av);
        _exit(127);
    }
    close(fds[1]);
    int64_t cap = 4096, n = 0;
    char *buf = (char *)psd_alloc(cap);
    int timed_out = 0;
    time_t deadline = time(NULL) + (time_t)(timeout_sec > 0 ? timeout_sec : 0);
    for (;;) {
        int wait_ms = -1;
        if (timeout_sec > 0) {
            time_t left = deadline - time(NULL);
            if (left <= 0) { timed_out = 1; break; }
            wait_ms = (int)(left * 1000);
        }
        struct pollfd pfd = { fds[0], POLLIN, 0 };
        int pr = poll(&pfd, 1, wait_ms);
        if (pr == 0) { timed_out = 1; break; }
        if (pr < 0) {
            if (errno == EINTR) continue;
            break;
        }
        ssize_t r = read(fds[0], buf + n, (size_t)(cap - n));
        if (r <= 0) break;
        n += (int64_t)r;
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
    }
    if (timed_out) kill(pid, SIGKILL);
    /* drain whatever is left so the child can exit */
    ssize_t r;
    while ((r = read(fds[0], buf + n, (size_t)(cap - n))) > 0) {
        n += (int64_t)r;
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
    }
    close(fds[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    if (n > 0 && buf[n - 1] == '\n') n--;     /* strip trailing newline */
    psd_json *v = psd_json_from_str(psd_str_new(buf, n));
    free(buf);
    if (timed_out) {
        char msg[64];
        snprintf(msg, sizeof msg, "timeout after %llds", (long long)timeout_sec);
        return psd_try_err2(msg, "code", psd_json_from_int(-1), "value", v);
    }
    int code = psd_wait_code(status);
    if (code != 0) {
        char msg[64];
        snprintf(msg, sizeof msg, "exit %d", code);
        return psd_try_err2(msg, "code", psd_json_from_int(code), "value", v);
    }
    return psd_try_ok(v);
}

psd_json *psd_try_capture(psd_list *argv_list, int64_t timeout_sec) {
    char **av = psd_argv_array(argv_list);
    psd_json *out = psd_capture_core(av, NULL, timeout_sec);
    free(av);
    return out;
}

psd_json *psd_try_capture_shell(psd_str *cmd, int64_t timeout_sec) {
    return psd_capture_core(NULL, cmd->data, timeout_sec);
}

/* run() without captured output, but recoverable: {ok, code} as data.
 * The child's stdout/stderr pass through (it is a launcher, not a pipe). */
psd_json *psd_try_run(psd_list *argv_list, int64_t timeout_sec) {
    psd_cap_check("PSD_DISABLE_PROCESS", "process execution");
    char **av = psd_argv_array(argv_list);
    fflush(stdout);
    pid_t pid = fork();
    if (pid < 0) { free(av); return psd_try_err("fork failed"); }
    if (pid == 0) {
        execvp(av[0], av);
        _exit(127);
    }
    free(av);
    int status = 0;
    if (timeout_sec > 0) {
        time_t deadline = time(NULL) + (time_t)timeout_sec;
        for (;;) {
            pid_t w = waitpid(pid, &status, WNOHANG);
            if (w == pid) break;
            if (w < 0) break;
            if (time(NULL) >= deadline) {
                kill(pid, SIGKILL);
                waitpid(pid, &status, 0);
                return psd_try_err2("timeout", "code",
                                    psd_json_from_int(-1), NULL, NULL);
            }
            struct timespec ts = {0, 10000000L};   /* 10ms poll */
            nanosleep(&ts, NULL);
        }
    } else {
        waitpid(pid, &status, 0);
    }
    int code = psd_wait_code(status);
    if (code != 0) {
        char msg[64];
        snprintf(msg, sizeof msg, "exit %d", code);
        return psd_try_err2(msg, "code", psd_json_from_int(code), NULL, NULL);
    }
    psd_map *m = psd_map_new(PSD_ELEM_JSON, 2);
    psd_json *ok_j = psd_json_from_bool(1);
    psd_map_set_k(m, psd_str_new("ok", 2), (int64_t)(intptr_t)ok_j);
    psd_json_decref(ok_j);
    psd_json *code_j = psd_json_from_int(0);
    psd_map_set_k(m, psd_str_new("code", 4), (int64_t)(intptr_t)code_j);
    psd_json_decref(code_j);
    return psd_json_from_map(m);
}

/* web serving ------------------------------------------------------------
 * Minimal HTTP/1.1 server (POSIX sockets). Sequential handling: one
 * request at a time, Connection: close. Binds 127.0.0.1 by default.
 */

static int psd_srv_fd = -1;
static int psd_cli_fd = -1;

psd_str *psd_url_decode(psd_str *s);   /* defined below */

int64_t psd_http_listen(int64_t port, psd_str *host) {
    psd_cap_check("PSD_DISABLE_HTTP", "http serving");
    if (psd_srv_fd >= 0) psd_panic("http_listen(): already listening");
    signal(SIGPIPE, SIG_IGN);
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) psd_panic("http_listen(): socket() failed");
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    const char *h = (host && host->len > 0) ? host->data : "127.0.0.1";
    if (inet_pton(AF_INET, h, &addr.sin_addr) != 1)
        psd_panic("http_listen(): invalid host (use an IPv4 address)");
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) != 0) {
        fprintf(stderr, "psd: runtime error: http_listen(): bind failed: %s\n",
                strerror(errno));
        exit(1);
    }
    if (listen(fd, 16) != 0) psd_panic("http_listen(): listen() failed");
    socklen_t alen = sizeof addr;
    if (getsockname(fd, (struct sockaddr *)&addr, &alen) != 0)
        psd_panic("http_listen(): getsockname() failed");
    psd_srv_fd = fd;
    fflush(NULL);   /* make earlier prints visible once we start serving */
    return (int64_t)ntohs(addr.sin_port);
}

static void psd_read_more(int fd, char **buf, int64_t *n, int64_t *cap,
                          int64_t limit) {
    if (*n >= limit) {
        fprintf(stderr, "psd: runtime error: http_accept(): request too large\n");
        exit(1);
    }
    if (*n + 1 >= *cap) {          /* keep one spare byte for NUL termination */
        *cap = *cap * 2 > limit ? limit : *cap * 2;
        if (*cap <= *n + 1) *cap = *n + 2;
        *buf = (char *)realloc(*buf, (size_t)*cap);
        if (!*buf) psd_panic("out of memory");
    }
    ssize_t r = read(fd, *buf + *n, (size_t)(*cap - *n - 1));
    if (r < 0) psd_panic("http_accept(): read failed");
    if (r == 0) psd_panic("http_accept(): client closed connection early");
    *n += (int64_t)r;
}

psd_json *psd_http_accept(void) {
    if (psd_srv_fd < 0) psd_panic("http_accept(): call http_listen() first");
    if (psd_cli_fd >= 0) { close(psd_cli_fd); psd_cli_fd = -1; }
    fflush(NULL);   /* flush logs before blocking on the next request */
    int c = accept(psd_srv_fd, NULL, NULL);
    if (c < 0) psd_panic("http_accept(): accept() failed");
    psd_cli_fd = c;

    /* read until end of headers */
    int64_t cap = 4096, n = 0;
    char *buf = (char *)psd_alloc(cap);
    buf[0] = '\0';
    char *hdr_end = NULL;
    while (!(hdr_end = strstr(buf, "\r\n\r\n"))) {
        psd_read_more(c, &buf, &n, &cap, 65536);
        buf[n] = '\0';
    }
    int64_t hdr_len = (int64_t)(hdr_end - buf) + 4;

    /* request line: METHOD SP TARGET SP HTTP/x.y */
    char *line_end = strstr(buf, "\r\n");
    if (!line_end) psd_panic("http_accept(): malformed request");
    *line_end = '\0';
    char *sp1 = strchr(buf, ' ');
    if (!sp1) psd_panic("http_accept(): malformed request line");
    *sp1 = '\0';
    char *method = buf;
    char *target = sp1 + 1;
    char *sp2 = strchr(target, ' ');
    if (!sp2) psd_panic("http_accept(): malformed request line");
    *sp2 = '\0';
    char *qmark = strchr(target, '?');
    char *query = "";
    if (qmark) { *qmark = '\0'; query = qmark + 1; }

    /* Headers: Content-Length frames the body; every header is also
     * exposed as req["headers"] (lowercased names, last value wins) so
     * apps can read Content-Type, cookies, auth, etc. */
    int64_t content_len = 0;
    psd_map *hdrs = psd_map_new(PSD_ELEM_JSON, 8);
    for (char *p = line_end + 2; p < hdr_end; ) {
        char *e = strstr(p, "\r\n");
        if (!e) break;
        *e = '\0';
        if (strncasecmp(p, "Content-Length:", 15) == 0)
            content_len = strtoll(p + 15, NULL, 10);
        char *colon = strchr(p, ':');
        if (colon && colon != p) {
            *colon = '\0';
            char *val = colon + 1;
            while (*val == ' ' || *val == '\t') val++;
            for (char *k = p; *k; k++) *k = (char)tolower((unsigned char)*k);
            psd_str *ks = psd_str_new(p, (int64_t)strlen(p));
            psd_str *vs = psd_str_new(val, (int64_t)strlen(val));
            psd_json *vj = psd_json_from_str(vs);
            psd_map_set_k(hdrs, ks, (int64_t)(intptr_t)vj);
            psd_json_decref(vj);
        }
        p = e + 2;
    }
    if (content_len < 0) content_len = 0;
    if (content_len > 8 * 1024 * 1024) {
        fprintf(stderr, "psd: runtime error: http_accept(): request body too large\n");
        exit(1);
    }

    /* body */
    int64_t have = n - hdr_len;
    while (have < content_len) {
        if (n == cap) {
            cap *= 2;
            buf = (char *)realloc(buf, (size_t)cap);
            if (!buf) psd_panic("out of memory");
        }
        ssize_t r = read(c, buf + n, (size_t)(cap - n));
        if (r <= 0) psd_panic("http_accept(): truncated request body");
        n += (int64_t)r;
        have = n - hdr_len;
    }

    psd_str *m_s = psd_str_new(method, (int64_t)strlen(method));
    psd_str *raw_p = psd_str_new(target, (int64_t)strlen(target));
    psd_str *p_s = psd_url_decode(raw_p);
    psd_str_decref(raw_p);            /* url_decode borrows its input */
    psd_str *q_s = psd_str_new(query, (int64_t)strlen(query));
    psd_str *b_s = psd_str_new(buf + hdr_len, content_len);
    free(buf);

    /* psd_json_from_str CONSUMES the str reference — no decref afterwards */
    psd_map *m = psd_map_new(PSD_ELEM_JSON, 4);
    psd_json *j;
    j = psd_json_from_str(m_s);
    psd_map_set_k(m, psd_str_new("method", 6), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_str(p_s);
    psd_map_set_k(m, psd_str_new("path", 4), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_str(q_s);
    psd_map_set_k(m, psd_str_new("query", 5), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_str(b_s);
    psd_map_set_k(m, psd_str_new("body", 4), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_map(hdrs);
    psd_map_set_k(m, psd_str_new("headers", 7), (int64_t)(intptr_t)j); psd_json_decref(j);
    return psd_json_from_map(m);
}

static const char *psd_reason(int64_t status) {
    switch (status) {
        case 200: return "OK";
        case 201: return "Created";
        case 204: return "No Content";
        case 302: return "Found";
        case 303: return "See Other";
        case 400: return "Bad Request";
        case 404: return "Not Found";
        case 405: return "Method Not Allowed";
        case 413: return "Payload Too Large";
        case 500: return "Internal Server Error";
        default:  return "Status";
    }
}

void psd_http_reply(int64_t status, psd_str *content_type, psd_str *body) {
    if (psd_cli_fd < 0) psd_panic("http_reply(): no pending request");
    char head[512];
    int hl = snprintf(head, sizeof head,
                      "HTTP/1.1 %lld %s\r\nContent-Type: %s\r\n"
                      "Content-Length: %lld\r\nConnection: close\r\n\r\n",
                      (long long)status, psd_reason(status),
                      content_type->data, (long long)body->len);
    int64_t sent = 0;
    while (sent < hl) {
        ssize_t w = write(psd_cli_fd, head + sent, (size_t)(hl - sent));
        if (w <= 0) break;
        sent += w;
    }
    sent = 0;
    while (sent < body->len) {
        ssize_t w = write(psd_cli_fd, body->data + sent, (size_t)(body->len - sent));
        if (w <= 0) break;
        sent += w;
    }
    close(psd_cli_fd);
    psd_cli_fd = -1;
}

/* 302 redirect: the canonical post-form / login-flow response. */
void psd_http_redirect(psd_str *url) {
    if (psd_cli_fd < 0) psd_panic("http_redirect(): no pending request");
    char head[8192];
    int hl = snprintf(head, sizeof head,
                      "HTTP/1.1 302 Found\r\nLocation: %s\r\n"
                      "Content-Length: 0\r\nConnection: close\r\n\r\n",
                      url->data);
    if (hl > (int)sizeof head - 1) hl = (int)sizeof head - 1;
    int64_t sent = 0;
    while (sent < hl) {
        ssize_t w = write(psd_cli_fd, head + sent, (size_t)(hl - sent));
        if (w <= 0) break;
        sent += w;
    }
    close(psd_cli_fd);
    psd_cli_fd = -1;
}

/* Stream a file as the response (no whole-file string in memory):
 * the fast path for images, downloads, and PWA/static assets.
 * A missing file answers 404 text/plain, like a real static server. */
void psd_http_reply_file(psd_str *path, psd_str *content_type) {
    if (psd_cli_fd < 0) psd_panic("http_reply_file(): no pending request");
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    FILE *f = fopen(path->data, "rb");
    if (!f) {
        psd_str *body = psd_str_new("not found", 9);
        psd_str *ct = psd_str_new("text/plain", 10);
        psd_http_reply(404, ct, body);
        psd_str_decref(body);
        psd_str_decref(ct);
        return;
    }
    struct stat st;
    int64_t size = 0;
    if (fstat(fileno(f), &st) == 0) size = (int64_t)st.st_size;
    char head[512];
    int hl = snprintf(head, sizeof head,
                      "HTTP/1.1 200 %s\r\nContent-Type: %s\r\n"
                      "Content-Length: %lld\r\nConnection: close\r\n\r\n",
                      psd_reason(200), content_type->data, (long long)size);
    int64_t sent = 0;
    while (sent < hl) {
        ssize_t w = write(psd_cli_fd, head + sent, (size_t)(hl - sent));
        if (w <= 0) break;
        sent += w;
    }
    char chunk[65536];
    size_t r;
    while ((r = fread(chunk, 1, sizeof chunk, f)) > 0) {
        int64_t off = 0;
        while (off < (int64_t)r) {
            ssize_t w = write(psd_cli_fd, chunk + off, (size_t)((int64_t)r - off));
            if (w <= 0) break;
            off += w;
        }
    }
    fclose(f);
    close(psd_cli_fd);
    psd_cli_fd = -1;
}

/*Concurrent prefork server: like http_listen, but forks `workers`
 * processes that share the listen socket, so requests are handled in
 * parallel (the Python equivalent needs gunicorn; here it is a builtin).
 * The parent process supervises and never returns; worker processes
 * return and run the program's accept loop. State is per-process —
 * share via files/json, not variables. Stop with Ctrl-C / SIGINT to the
 * process group (terminal signals reach every worker). */
int64_t psd_http_listen_pool(int64_t port, psd_str *host, int64_t workers) {
    if (workers < 1) workers = 1;
    if (workers > 64) workers = 64;
    int64_t bound = psd_http_listen(port, host);
    if (workers == 1) return bound;
    fflush(NULL);
    int spawned = 0;
    for (int64_t i = 0; i < workers; i++) {
        pid_t pid = fork();
        if (pid < 0) break;
        if (pid == 0) return bound;          /* worker: run accept loop */
        spawned++;
    }
    /* parent: supervise; exit when every worker is gone */
    signal(SIGTERM, SIG_DFL);
    int alive = spawned;
    while (alive > 0) {
        int status = 0;
        if (waitpid(-1, &status, 0) > 0) alive--;
    }
    exit(0);
}

/* html / url helpers for safe templating and form parsing */

psd_str *psd_html_escape(psd_str *s) {
    int64_t extra = 0;
    for (int64_t i = 0; i < s->len; i++)
        if (strchr("&<>\"'", s->data[i])) extra += 5;
    char *out = (char *)malloc((size_t)(s->len + extra + 1));
    if (!out) psd_panic("out of memory");
    int64_t j = 0;
    for (int64_t i = 0; i < s->len; i++) {
        const char *rep = NULL;
        switch (s->data[i]) {
            case '&': rep = "&amp;"; break;
            case '<': rep = "&lt;"; break;
            case '>': rep = "&gt;"; break;
            case '"': rep = "&quot;"; break;
            case '\'': rep = "&#39;"; break;
            default: out[j++] = s->data[i];
        }
        if (rep) { strcpy(out + j, rep); j += (int64_t)strlen(rep); }
    }
    out[j] = '\0';
    psd_str *r = psd_str_new(out, j);
    free(out);
    return r;
}

psd_str *psd_url_encode(psd_str *s) {
    char *out = (char *)malloc((size_t)(s->len * 3 + 1));
    if (!out) psd_panic("out of memory");
    static const char *hex = "0123456789ABCDEF";
    int64_t j = 0;
    for (int64_t i = 0; i < s->len; i++) {
        unsigned char c = (unsigned char)s->data[i];
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
            out[j++] = (char)c;
        } else {
            out[j++] = '%';
            out[j++] = hex[c >> 4];
            out[j++] = hex[c & 15];
        }
    }
    out[j] = '\0';
    psd_str *r = psd_str_new(out, j);
    free(out);
    return r;
}

psd_str *psd_url_decode(psd_str *s) {
    char *out = (char *)malloc((size_t)(s->len + 1));
    if (!out) psd_panic("out of memory");
    int64_t j = 0;
    for (int64_t i = 0; i < s->len; i++) {
        char c = s->data[i];
        if (c == '+' ) { out[j++] = ' '; }
        else if (c == '%' && i + 2 < s->len + 0 &&
                 isxdigit((unsigned char)s->data[i + 1]) &&
                 isxdigit((unsigned char)s->data[i + 2])) {
            char hex[3] = { s->data[i + 1], s->data[i + 2], 0 };
            out[j++] = (char)strtol(hex, NULL, 16);
            i += 2;
        } else out[j++] = c;
    }
    out[j] = '\0';
    psd_str *r = psd_str_new(out, j);
    free(out);
    return r;
}

double psd_sqrt(double x) {
    return sqrt(x);
}

/* ---- v0.4: math for ML, time, randomness, console, dirs, terminal --
 * exp/log/pow complete the float math needed to *train* models in PSD
 * (sigmoid/softmax, cross-entropy, gradient descent); domain errors use
 * IEEE semantics (log(-1) is NaN, log(0) is -inf) instead of crashing.
 * Terminal control + read_key serve interactive TUI programs and games;
 * open_url launches the browser UI as a desktop app; a small native image
 * engine (PPM/BMP) serves photo/image tools. */

double psd_exp(double x) { return exp(x); }
double psd_log(double x) { return log(x); }
double psd_pow_f64(double a, double b) { return pow(a, b); }

void psd_sleep_ms(int64_t ms) {
    if (ms <= 0) return;
    struct timespec ts;
    ts.tv_sec = (time_t)(ms / 1000);
    ts.tv_nsec = (long)((ms % 1000) * 1000000L);
    nanosleep(&ts, NULL);
}

int64_t psd_time_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static int psd_rand_seeded = 0;
static void psd_rand_seed(void) {
    if (psd_rand_seeded) return;
    psd_rand_seeded = 1;
    srand((unsigned)(time(NULL) ^ ((long)getpid() << 16)));
}

double psd_random(void) {           /* [0, 1) */
    psd_rand_seed();
    return (double)rand() / ((double)RAND_MAX + 1.0);
}

int64_t psd_rand_int(int64_t n) {    /* 0 .. n-1, like range(n) */
    if (n <= 0) psd_panic("rand_int(n): n must be positive");
    psd_rand_seed();
    return (int64_t)(psd_random() * (double)n) % n;
}

/* One line from stdin (newline stripped); "" at EOF. The prompt, if any,
 * goes to stdout and is flushed so interactive tools read naturally. */
psd_str *psd_read_line(psd_str *prompt) {
    if (prompt && prompt->len > 0) {
        fwrite(prompt->data, 1, (size_t)prompt->len, stdout);
        fflush(stdout);
    }
    char *line = NULL;
    size_t cap = 0;
    ssize_t got = getline(&line, &cap, stdin);
    if (got < 0) { free(line); return psd_str_new("", 0); }
    while (got > 0 && (line[got - 1] == '\n' || line[got - 1] == '\r')) got--;
    psd_str *s = psd_str_new(line, (int64_t)got);
    free(line);
    return s;
}

psd_str *psd_cwd(void) {
    char buf[4096];
    if (!getcwd(buf, sizeof buf)) psd_panic("cwd(): cannot get working directory");
    return psd_str_new(buf, (int64_t)strlen(buf));
}

/* mkdir -p semantics: creates missing parents, and succeeding when the
 * directory already exists keeps setup scripts simple (`make_dir("out")` at
 * the top of every run). */
static int psd_mkdir_p(const char *path) {
    if (!path || !*path) return -1;
    size_t len = strlen(path);
    if (len > 4095) return -1;
    char buf[4096];
    memcpy(buf, path, len + 1);
    /* walk every '/' and create the prefix up to it */
    for (char *p = buf + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (mkdir(buf, 0777) != 0 && errno != EEXIST) return -1;
            *p = '/';
        }
    }
    if (mkdir(buf, 0777) != 0 && errno != EEXIST) return -1;
    return 0;
}

void psd_make_dir(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    if (psd_mkdir_p(path->data) != 0) {
        psd_str *msg = psd_str_concat(psd_str_new("cannot create directory: ", 25), path);
        psd_panic(msg->data);
    }
}

/* terminal (desktop TUI + games) */
void psd_term_clear(void) { fputs("\033[2J\033[H", stdout); fflush(stdout); }
void psd_term_home(void) { fputs("\033[H", stdout); fflush(stdout); }

psd_json *psd_term_size(void) {
    struct winsize ws;
    int64_t cols = 80, rows = 24;
    if (ioctl(1, TIOCGWINSZ, &ws) == 0 && ws.ws_col > 0) {
        cols = ws.ws_col;
        rows = ws.ws_row;
    }
    psd_map *m = psd_map_new(PSD_ELEM_JSON, 2);
    psd_json *j = psd_json_from_int(cols);
    psd_map_set_k(m, psd_str_new("cols", 4), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_int(rows);
    psd_map_set_k(m, psd_str_new("rows", 4), (int64_t)(intptr_t)j); psd_json_decref(j);
    return psd_json_from_map(m);
}

/* One keypress as a string: printable chars as-is, ENTER as "enter",
 * arrows as "up"/"down"/"left"/"right", ESC as "esc", "" at EOF.
 * On a TTY it reads a single raw keypress (no Enter needed). */
psd_str *psd_read_key(void) {
    int tty = isatty(0);
    struct termios oldt, raw;
    if (tty) {
        tcgetattr(0, &oldt);
        raw = oldt;
        raw.c_lflag &= (tcflag_t)~(ICANON | ECHO);
        raw.c_cc[VMIN] = 1;
        raw.c_cc[VTIME] = 0;
        tcsetattr(0, TCSANOW, &raw);
    }
    int c = getchar();
    if (tty) tcsetattr(0, TCSANOW, &oldt);
    if (c == EOF) return psd_str_new("", 0);
    if (c == '\n' || c == '\r') return psd_str_new("enter", 5);
    if (c == 27) {
        struct pollfd pfd = {0, POLLIN, 0};
        if (poll(&pfd, 1, 30) > 0 && getchar() == '[') {
            int a = getchar();
            if (a == 'A') return psd_str_new("up", 2);
            if (a == 'B') return psd_str_new("down", 4);
            if (a == 'C') return psd_str_new("right", 5);
            if (a == 'D') return psd_str_new("left", 4);
        }
        return psd_str_new("esc", 3);
    }
    char b[2] = {(char)c, 0};
    return psd_str_new(b, 1);
}

/* ---- native image engine (PPM P3/P6 + BMP 24-bit) ----------------------
 * File-to-file operations returning try_*-style envelopes, so PSD gets
 * real image tools without a codec dependency: load either format, apply
 * the op in C (fast pixel loops), write .ppm/.bmp chosen by extension.
 * Ops: grayscale | invert | flip_h | flip_v | blur | brightness (arg =
 * percent, 100 = unchanged) | resize (arg = new width px, keeps ratio). */

typedef struct { int64_t w, h; unsigned char *rgb; int is_bmp; } psd_img;

static unsigned char *psd_read_all(const char *path, int64_t *len_out) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    int64_t cap = 65536, n = 0;
    unsigned char *buf = (unsigned char *)psd_alloc(cap);
    size_t r;
    while ((r = fread(buf + n, 1, (size_t)(cap - n), f)) > 0) {
        n += (int64_t)r;
        if (n == cap) { cap *= 2; buf = (unsigned char *)realloc(buf, (size_t)cap); }
    }
    fclose(f);
    *len_out = n;
    return buf;
}

static int psd_img_token(const unsigned char *d, int64_t n, int64_t *p,
                         char *out, int64_t cap) {
    while (*p < n && (isspace(d[*p]) || d[*p] == '#')) {
        if (d[*p] == '#') while (*p < n && d[*p] != '\n') (*p)++;
        else (*p)++;
    }
    int64_t i = 0;
    while (*p < n && !isspace(d[*p]) && i + 1 < cap) out[i++] = (char)d[(*p)++];
    out[i] = 0;
    return i > 0;
}

static int psd_img_load(const char *path, psd_img *im) {
    int64_t n = 0;
    unsigned char *d = psd_read_all(path, &n);
    if (!d) return 0;
    memset(im, 0, sizeof *im);
    if (n > 2 && d[0] == 'P' && (d[1] == '6' || d[1] == '3')) {
        int binary = d[1] == '6';
        int64_t p = 2;
        char tok[64];
        if (!psd_img_token(d, n, &p, tok, sizeof tok)) goto fail;
        im->w = strtoll(tok, NULL, 10);
        if (!psd_img_token(d, n, &p, tok, sizeof tok)) goto fail;
        im->h = strtoll(tok, NULL, 10);
        if (!psd_img_token(d, n, &p, tok, sizeof tok)) goto fail;
        if (strtoll(tok, NULL, 10) != 255 || im->w <= 0 || im->h <= 0 ||
            im->w * im->h > 100000000) goto fail;
        im->rgb = (unsigned char *)psd_alloc(im->w * im->h * 3);
        if (binary) {
            p++;                                /* single whitespace */
            if (p + im->w * im->h * 3 > n) goto fail;
            memcpy(im->rgb, d + p, (size_t)(im->w * im->h * 3));
        } else {
            for (int64_t i = 0; i < im->w * im->h * 3; i++) {
                if (!psd_img_token(d, n, &p, tok, sizeof tok)) goto fail;
                im->rgb[i] = (unsigned char)strtoll(tok, NULL, 10);
            }
        }
        free(d);
        return 1;
    }
    if (n > 54 && d[0] == 'B' && d[1] == 'M') {
        uint32_t off, dib;
        int32_t w, h;
        uint16_t planes, bpp;
        uint32_t comp;
        memcpy(&off, d + 10, 4); memcpy(&dib, d + 14, 4);
        memcpy(&w, d + 18, 4); memcpy(&h, d + 22, 4);
        memcpy(&planes, d + 26, 2); memcpy(&bpp, d + 28, 2);
        memcpy(&comp, d + 30, 4);
        if (dib < 40 || planes != 1 || bpp != 24 || comp != 0 || w <= 0 || h == 0)
            goto fail;
        int64_t hh = h < 0 ? -(int64_t)h : h;
        int64_t row = ((int64_t)w * 3 + 3) & ~3LL;
        if ((int64_t)off + row * hh > n) goto fail;
        im->w = w; im->h = hh; im->is_bmp = 1;
        im->rgb = (unsigned char *)psd_alloc(w * hh * 3);
        for (int64_t y = 0; y < hh; y++) {
            int64_t src_y = h > 0 ? hh - 1 - y : y;
            unsigned char *rowp = d + off + src_y * row;
            for (int64_t x = 0; x < w; x++) {   /* BGR -> RGB */
                im->rgb[(y * w + x) * 3 + 0] = rowp[x * 3 + 2];
                im->rgb[(y * w + x) * 3 + 1] = rowp[x * 3 + 1];
                im->rgb[(y * w + x) * 3 + 2] = rowp[x * 3 + 0];
            }
        }
        free(d);
        return 1;
    }
fail:
    free(d);
    free(im->rgb);
    im->rgb = NULL;
    return 0;
}

static int psd_img_save(const char *path, const psd_img *im) {
    FILE *f = fopen(path, "wb");
    if (!f) return 0;
    const char *dot = strrchr(path, '.');
    int to_bmp = dot && (strcmp(dot, ".bmp") == 0);
    if (to_bmp) {
        int64_t row = ((im->w * 3) + 3) & ~3LL;
        int64_t px = row * im->h;
        unsigned char hdr[54];
        memset(hdr, 0, sizeof hdr);
        hdr[0] = 'B'; hdr[1] = 'M';
        uint32_t total = (uint32_t)(54 + px), off = 54, dib = 40;
        int32_t w = (int32_t)im->w, h = (int32_t)im->h;
        uint16_t planes = 1, bpp = 24;
        memcpy(hdr + 2, &total, 4); memcpy(hdr + 10, &off, 4);
        memcpy(hdr + 14, &dib, 4); memcpy(hdr + 18, &w, 4);
        memcpy(hdr + 22, &h, 4); memcpy(hdr + 26, &planes, 2);
        memcpy(hdr + 28, &bpp, 2);
        fwrite(hdr, 1, 54, f);
        unsigned char *rowbuf = (unsigned char *)psd_alloc(row);
        for (int64_t y = im->h - 1; y >= 0; y--) {
            memset(rowbuf, 0, (size_t)row);
            for (int64_t x = 0; x < im->w; x++) {
                rowbuf[x * 3 + 0] = im->rgb[(y * im->w + x) * 3 + 2];
                rowbuf[x * 3 + 1] = im->rgb[(y * im->w + x) * 3 + 1];
                rowbuf[x * 3 + 2] = im->rgb[(y * im->w + x) * 3 + 0];
            }
            fwrite(rowbuf, 1, (size_t)row, f);
        }
        free(rowbuf);
    } else {
        fprintf(f, "P6\n%lld %lld\n255\n", (long long)im->w, (long long)im->h);
        fwrite(im->rgb, 1, (size_t)(im->w * im->h * 3), f);
    }
    int bad = ferror(f);
    fclose(f);
    return !bad;
}

static psd_json *psd_img_result(const psd_img *im, const char *fmt) {
    psd_map *m = psd_map_new(PSD_ELEM_JSON, 5);
    psd_json *j = psd_json_from_bool(1);
    psd_map_set_k(m, psd_str_new("ok", 2), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_int(im->w);
    psd_map_set_k(m, psd_str_new("width", 5), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_int(im->h);
    psd_map_set_k(m, psd_str_new("height", 6), (int64_t)(intptr_t)j); psd_json_decref(j);
    j = psd_json_from_str(psd_str_new(fmt, (int64_t)strlen(fmt)));
    psd_map_set_k(m, psd_str_new("format", 6), (int64_t)(intptr_t)j); psd_json_decref(j);
    return psd_json_from_map(m);
}

psd_json *psd_image_info(psd_str *path) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    psd_img im;
    if (!psd_img_load(path->data, &im)) return psd_try_err("cannot read image (want PPM or BMP)");
    psd_json *r = psd_img_result(&im, im.is_bmp ? "bmp" : "ppm");
    free(im.rgb);
    return r;
}

psd_json *psd_image_process(psd_str *in_path, psd_str *out_path,
                            psd_str *op, int64_t arg) {
    psd_cap_check("PSD_DISABLE_FILES", "file access");
    psd_img im;
    if (!psd_img_load(in_path->data, &im))
        return psd_try_err("cannot read image (want PPM or BMP)");
    const char *o = op->data;
    int64_t npix = im.w * im.h;
    if (strcmp(o, "grayscale") == 0) {
        for (int64_t i = 0; i < npix; i++) {
            unsigned char *px = im.rgb + i * 3;
            unsigned char g = (unsigned char)((px[0] * 299 + px[1] * 587 + px[2] * 114) / 1000);
            px[0] = px[1] = px[2] = g;
        }
    } else if (strcmp(o, "invert") == 0) {
        for (int64_t i = 0; i < npix * 3; i++) im.rgb[i] = (unsigned char)(255 - im.rgb[i]);
    } else if (strcmp(o, "brightness") == 0) {
        for (int64_t i = 0; i < npix * 3; i++) {
            int64_t v = im.rgb[i] * (arg > 0 ? arg : 100) / 100;
            im.rgb[i] = (unsigned char)(v > 255 ? 255 : v);
        }
    } else if (strcmp(o, "flip_h") == 0 || strcmp(o, "flip_v") == 0) {
        unsigned char *copy = (unsigned char *)psd_alloc(npix * 3);
        memcpy(copy, im.rgb, (size_t)(npix * 3));
        for (int64_t y = 0; y < im.h; y++)
            for (int64_t x = 0; x < im.w; x++) {
                int64_t sx = strcmp(o, "flip_h") == 0 ? im.w - 1 - x : x;
                int64_t sy = strcmp(o, "flip_v") == 0 ? im.h - 1 - y : y;
                memcpy(im.rgb + (y * im.w + x) * 3, copy + (sy * im.w + sx) * 3, 3);
            }
        free(copy);
    } else if (strcmp(o, "blur") == 0) {
        unsigned char *copy = (unsigned char *)psd_alloc(npix * 3);
        memcpy(copy, im.rgb, (size_t)(npix * 3));
        for (int64_t y = 0; y < im.h; y++)
            for (int64_t x = 0; x < im.w; x++)
                for (int c = 0; c < 3; c++) {
                    int64_t sum = 0, cnt = 0;
                    for (int64_t dy = -1; dy <= 1; dy++)
                        for (int64_t dx = -1; dx <= 1; dx++) {
                            int64_t xx = x + dx, yy = y + dy;
                            if (xx < 0 || yy < 0 || xx >= im.w || yy >= im.h) continue;
                            sum += copy[(yy * im.w + xx) * 3 + c];
                            cnt++;
                        }
                    im.rgb[(y * im.w + x) * 3 + c] = (unsigned char)(sum / cnt);
                }
        free(copy);
    } else if (strcmp(o, "resize") == 0) {
        if (arg <= 0 || arg > 100000) { free(im.rgb); return psd_try_err("resize: arg must be the new width in px"); }
        int64_t nw = arg;
        int64_t nh = im.h * nw / im.w;
        if (nh < 1) nh = 1;
        unsigned char *out = (unsigned char *)psd_alloc(nw * nh * 3);
        for (int64_t y = 0; y < nh; y++)
            for (int64_t x = 0; x < nw; x++)
                memcpy(out + (y * nw + x) * 3,
                       im.rgb + ((y * im.h / nh) * im.w + (x * im.w / nw)) * 3, 3);
        free(im.rgb);
        im.rgb = out; im.w = nw; im.h = nh;
    } else {
        free(im.rgb);
        return psd_try_err("unknown image op (grayscale|invert|flip_h|flip_v|blur|brightness|resize)");
    }
    if (!psd_img_save(out_path->data, &im)) {
        free(im.rgb);
        return psd_try_err("cannot write output image");
    }
    const char *dot = strrchr(out_path->data, '.');
    psd_json *r = psd_img_result(&im, (dot && strcmp(dot, ".bmp") == 0) ? "bmp" : "ppm");
    free(im.rgb);
    return r;
}

/* Open a URL/file in the desktop browser — the "desktop app" launcher:
 * serve on localhost with http_listen, then open_url("http://127.0.0.1:N"). */
int64_t psd_open_url(psd_str *url) {
    psd_cap_check("PSD_DISABLE_PROCESS", "process execution");
    fflush(stdout);
    pid_t pid = fork();
    if (pid < 0) return 0;
    if (pid == 0) {
#ifdef __APPLE__
        execlp("open", "open", url->data, (char *)NULL);
#else
        execlp("xdg-open", "xdg-open", url->data, (char *)NULL);
#endif
        _exit(127);
    }
    return 1;
}

/* process: args / env / exit -------------------------------------------- */

static int psd_argc = 0;
static char **psd_argv = NULL;

void psd_set_args(int argc, char **argv) {
    psd_argc = argc;
    psd_argv = argv;
}

psd_list *psd_args(void) {
    /* arguments after the program name */
    psd_list *l = psd_list_new(PSD_ELEM_STR, psd_argc > 0 ? psd_argc - 1 : 0);
    for (int i = 1; i < psd_argc; i++) {
        psd_str *s = psd_str_new(psd_argv[i], (int64_t)strlen(psd_argv[i]));
        psd_list_push(l, (int64_t)(intptr_t)s);
        psd_str_decref(s);
    }
    return l;
}

psd_str *psd_env(psd_str *name, psd_str *fallback) {
    const char *v = getenv(name->data);
    if (v) return psd_str_new(v, (int64_t)strlen(v));
    if (fallback) {
        psd_str_incref(fallback);
        return fallback;
    }
    psd_str *msg = psd_str_concat(psd_str_new("environment variable not set: ", 30), name);
    psd_panic(msg->data);
    return NULL;
}

void psd_exit(int64_t code) {
    fflush(stdout);
    fflush(stderr);
    exit((int)code);
}
