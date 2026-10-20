/* Pattern: the third branch is the XOR of two earlier random branches.
   gshare can learn it from history; a perceptron cannot (XOR is not linearly separable). */
volatile int sink;
static unsigned int state = 12345;

static unsigned int rnd(void) {            /* xorshift32 pseudo-random numbers */
    state ^= state << 13; state ^= state >> 17; state ^= state << 5;
    return state;
}

int main(void) {
    for (int i = 0; i < 3000; i++) {
        unsigned int r = rnd();
        int a = r & 1, b = (r >> 1) & 1;
        if (a) sink = 1;                   /* random */
        if (b) sink = 2;                   /* random */
        if (a ^ b) sink = 3;               /* fully determined by the two branches above */
    }
    return 0;
}
