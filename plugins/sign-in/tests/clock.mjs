// Explicit logical time: CPU scheduling never advances a test deadline.
export function manualClock(){
 let time=0,next=0;const timers=new Map();
 const schedule=(fn,ms,repeat=false)=>{const handle={id:++next,unref(){return this;}};timers.set(handle,{fn,at:time+ms,ms,repeat});return handle;};
 const clock={now:()=>time,setTimeout:(fn,ms)=>schedule(fn,ms),clearTimeout:h=>timers.delete(h),setInterval:(fn,ms)=>schedule(fn,ms,true),clearInterval:h=>timers.delete(h),get pending(){return timers.size;},
  async advance(ms){const end=time+ms;while(true){const due=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;const [handle,timer]=due;time=timer.at;if(timer.repeat)timer.at+=timer.ms;else timers.delete(handle);await timer.fn();await Promise.resolve();}time=end;await Promise.resolve();}
 };
 clock.sleep=ms=>new Promise(resolve=>clock.setTimeout(resolve,ms));return clock;
}
