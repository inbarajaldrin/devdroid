// Deadlines use real time in production; tests advance an injected clock explicitly.
export const systemClock={now:Date.now,setTimeout,clearTimeout,setInterval,clearInterval,sleep:ms=>new Promise(resolve=>setTimeout(resolve,ms))};
