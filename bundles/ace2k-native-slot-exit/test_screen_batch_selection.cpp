#include "screen_batch_selection.h"
#include <cassert>
using helix::u1_filament::BatchSelection;
int main() {
    BatchSelection s;
    assert(s.empty());
    assert(!s.toggle({-1,0,0,"serial"}));
    assert(!s.toggle({0,0,4,""}));
    for(int a=0;a<4;++a) for(int n=0;n<4;++n)
        assert(s.toggle({a,n,4+4*a+n,"uid"+std::to_string(a)}));
    assert(s.entries().size()==16);
    assert(!s.distinct_heads());
    assert(s.contains(3,3));
    assert(s.toggle({3,3,19,"uid3"}));
    assert(!s.contains(3,3));
    assert(s.entries().size()==15);
    s.clear();
    for(int n=0;n<4;++n) assert(s.toggle({n,n,4+5*n,"uid"+std::to_string(n)}));
    assert(s.distinct_heads());
    assert(s.toggle({0,1,5,"uid0"}));
    assert(!s.distinct_heads());
}
