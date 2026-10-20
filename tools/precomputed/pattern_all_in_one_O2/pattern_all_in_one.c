/* All the pattern demos in one program: one function per pattern, run one after the other.
   In the dashboard, "Jump to" goes straight to a section. What each section shows:
     alternating  T, N, T, N: bimodal 50%, the history predictors learn it
     loop4        exit of a 4-iteration loop: the history predictors learn the exit
     loop12       exit of a 12-iteration loop: too far back for 7-8 history bits, nobody learns it
     correlated   a branch repeating an earlier random one: bimodal ~50%, gshare and perceptron ~100%
     xor_pattern  XOR of two random branches: no predictor here learns it (gshare would, with 6 history bits)
     random_flip  a fair coin: everyone ~50%
     biased       90% one way at random: the bias helps (bimodal ~90%), history does not
   The sections share the chip's tables, so a few branches can alias across sections; the separate
   pattern_* programs show each behaviour on its own. */
volatile int sink;
static unsigned int state = 2463534242u;

static unsigned int rnd(void) {            /* xorshift32 pseudo-random numbers */
    state ^= state << 13; state ^= state >> 17; state ^= state << 5;
    return state;
}

__attribute__((noinline)) static void alternating(void) {
    for (int i = 0; i < 2000; i++) {
        if (i & 1) sink = i;               /* T, N, T, N, ... */
        else       sink = -i;
    }
}

__attribute__((noinline)) static void loop4(void) {
    for (int i = 0; i < 500; i++)
        for (int j = 0; j < 4; j++)        /* exit every 4th time */
            sink = i + j;
}

__attribute__((noinline)) static void loop12(void) {
    for (int i = 0; i < 200; i++)
        for (int j = 0; j < 12; j++)       /* exit every 12th time */
            sink = i + j;
}

__attribute__((noinline)) static void correlated(void) {
    for (int i = 0; i < 1500; i++) {
        int x = rnd() & 1;
        if (x) sink = 1;                   /* random */
        for (int k = 0; k < 2; k++) sink = k;
        if (x) sink = 2;                   /* repeats the branch two lines up */
    }
}

__attribute__((noinline)) static void xor_pattern(void) {
    for (int i = 0; i < 1500; i++) {
        unsigned int r = rnd();
        int a = r & 1, b = (r >> 1) & 1;
        if (a) sink = 1;                   /* random */
        if (b) sink = 2;                   /* random */
        if (a ^ b) sink = 3;               /* fully determined by the two above */
    }
}

__attribute__((noinline)) static void random_flip(void) {
    for (int i = 0; i < 2000; i++)
        if (rnd() & 1) sink = 1;           /* 50 / 50 */
}

__attribute__((noinline)) static void biased(void) {
    for (int i = 0; i < 2000; i++)
        if (rnd() % 10 != 0) sink = 1;     /* 9 times in 10 */
}

int main(void) {
    alternating();
    loop4();
    loop12();
    correlated();
    xor_pattern();
    random_flip();
    biased();
    return 0;
}
