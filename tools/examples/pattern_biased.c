/* Pattern: a random branch that goes the same way 90% of the time (line 15).
   History does not help (each outcome is independent), but the bias does: bimodal's 2-bit counter settles on the
   common direction and gets about 90%. gshare and the perceptron can at best match it; their history bits only add
   noise. Compare with pattern_random (no bias: everyone 50%). */
volatile int sink;
static unsigned int state = 1234567u;

static unsigned int rnd(void) {            /* xorshift32 pseudo-random numbers */
    state ^= state << 13; state ^= state >> 17; state ^= state << 5;
    return state;
}

int main(void) {
    for (int i = 0; i < 4000; i++) {
        if (rnd() % 10 != 0) sink = 1;     /* 9 times in 10 */
    }
    return 0;
}
