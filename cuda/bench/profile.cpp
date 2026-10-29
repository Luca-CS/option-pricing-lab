// Target for Nsight Compute: one launch of each production kernel studied in the ablation, nothing else.
//
//   ncu --set full -k regex:mc_kernel -o cuda/bench/ncu_profile build\Release\profile.exe
#include <cstdio>

#include "optlab_cuda/gpu_mc.cuh"

using namespace optlab;

namespace {

McRequest request(Model m, Product p, int steps, long long n) {
    McRequest rq;
    rq.model = m;
    rq.product.product = p;
    rq.product.legs = {Leg::Call, Leg::None, Leg::None};
    rq.product.strikes = {100, 100, 100};
    rq.product.H = 80;
    rq.product.n_steps = steps;
    rq.n_paths = n;
    return rq;
}

}  // namespace

int main() {
    // Sizes chosen so that each request is a single launch (max_work = 2.5e8 path-steps).
    const McRequest cases[] = {
        request(Model::Heston, Product::Barrier, 252, 990'000),
        request(Model::BlackScholes, Product::Barrier, 252, 990'000),
        request(Model::BlackScholes, Product::European, 1, 100'000'000),
    };
    for (const McRequest& rq : cases) {
        GpuLaunchInfo info;
        const McResult r = gpu_price(rq, {}, &info);
        std::printf("%.6f +- %.6f  (%d launch(es), grid %d x %d)\n", r.out[0].price, r.out[0].se, info.launches,
                    info.grid, info.block);
    }
    return 0;
}
