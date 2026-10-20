/* Pattern: two branches on the same random condition, with a short fixed loop in between.
   The first (line 16) is a coin flip for every predictor. The second (line 18) always goes the same way as the
   first. Bimodal looks only at the branch itself and still sees a coin flip (about 50%); gshare and the perceptron
   find the first outcome 4 branches back in their global history and get the second right almost every time. */
volatile int sink;
static unsigned int state = 2463534242u;

static unsigned int rnd(void) {            /* xorshift32 pseudo-random numbers */
    state ^= state << 13; state ^= state >> 17; state ^= state << 5;
    return state;
}

int main(void) {
    for (int i = 0; i < 3000; i++) {
        int x = rnd() & 1;
        if (x) sink = 1;                   /* random */
        for (int k = 0; k < 2; k++) sink = k;   /* the same 3 outcomes every time */
        if (x) sink = 2;                   /* repeats the branch two lines up */
    }
    return 0;
}
