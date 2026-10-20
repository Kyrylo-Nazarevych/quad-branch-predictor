/* Workload: naive string search for "abca" in 2,000 pseudo-random letters from {a, b, c, d}.
   With only 4 letters, partial matches are common: the compare loop (line 19) runs 1, 2, 3 or 4 steps depending on
   the text, a data-dependent early exit like the binary search in sort_and_search. The "found it" branch is rare,
   which bias alone predicts well. */
#define LEN 2000
static char text[LEN];
static const char pat[4] = {'a', 'b', 'c', 'a'};
static unsigned int state = 362436069u;

static unsigned int rnd(void) {            /* xorshift32 pseudo-random numbers */
    state ^= state << 13; state ^= state >> 17; state ^= state << 5;
    return state;
}

static int count_matches(void) {
    int found = 0;
    for (int i = 0; i + 4 <= LEN; i++) {
        int k = 0;
        while (k < 4 && text[i + k] == pat[k]) k++;
        if (k == 4) found++;
    }
    return found;
}

int main(void) {
    for (int i = 0; i < LEN; i++) text[i] = (char)('a' + (rnd() & 3));
    return count_matches() & 0x7f;
}
