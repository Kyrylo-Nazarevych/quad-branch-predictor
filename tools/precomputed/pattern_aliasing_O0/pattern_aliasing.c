/* Pattern: 24 different branches, each of which always goes the same way (half always taken, half never).
   Alone, every one is trivial. But the chip picks a perceptron with PC[4:2] (only 8 perceptrons) and a table entry
   with PC[7:2] (64 entries, 256 bytes of code): branches whose addresses share those bits share one entry, and when
   they want opposite directions they keep un-training each other. Watch the perceptron lose most to this aliasing;
   the tables suffer too once this loop is longer than 256 bytes of code. */
volatile int sink;
volatile int one = 1;                      /* read through volatile, so the compiler keeps every branch */

#define PAIR(n) if (one) sink = (n); if (!one) sink = (n) + 1;

int main(void) {
    for (int i = 0; i < 300; i++) {
        PAIR(0)  PAIR(2)  PAIR(4)  PAIR(6)  PAIR(8)  PAIR(10)
        PAIR(12) PAIR(14) PAIR(16) PAIR(18) PAIR(20) PAIR(22)
    }
    return 0;
}
