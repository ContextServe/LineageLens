/* Every construct the C spec claims, once. */

typedef int Meters;

struct Point {
    int x;
    int y;
};

union Value {
    int as_int;
    float as_float;
};

enum Color {
    RED,
    GREEN
};

/* A prototype: what a header exposes. */
int add(int a, int b);

int add(int a, int b) {
    return a + b;
}

static void noop(void) {
}
