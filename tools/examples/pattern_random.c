/* Pattern: a fair coin flip (line 14). Nothing about the past predicts it, so every predictor gets about 50% on it,
   whatever its size or history. The honest lower bound: a predictor can only learn what is regular.
   (The loop branch next to it is almost always taken, so whole-program accuracy is near 75%.) */
volatile int sink;
static unsigned int state = 88172645u;

static unsigned int rnd(void) {            /* xorshift32 pseudo-random numbers */
    state ^= state << 13; state ^= state >> 17; state ^= state << 5;
    return state;
}

int main(void) {
    for (int i = 0; i < 4000; i++) {
        if (rnd() & 1) sink = 1;           /* 50 / 50, independent of everything before */
    }
    return 0;
}
