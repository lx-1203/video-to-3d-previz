const fs=require('node:fs');const path=require('node:path');const {randomBytes}=require('node:crypto');
function loadConfig(root=path.join(__dirname,'..'),{port=47631}={}){
  const directory=path.join(root,'.local');fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const target=path.join(directory,'connection.json');
  if(!fs.existsSync(target)){
    const initial={protocol:1,port,token:randomBytes(32).toString('hex')};
    try{fs.writeFileSync(target,JSON.stringify(initial,null,2)+'\n',{flag:'wx',mode:0o600});}catch(error){if(error.code!=='EEXIST')throw error;}
  }
  let config;try{config=JSON.parse(fs.readFileSync(target,'utf8'));}catch{throw new Error('Invalid pairing file; repair .local/connection.json instead of silently rotating credentials.');}
  if(config?.protocol!==1||!Number.isSafeInteger(config.port)||config.port<1024||config.port>65535||!/^[a-f0-9]{64}$/.test(config.token||''))throw new Error('Invalid pairing file: protocol, port or token.');
  return{protocol:1,port:config.port,token:config.token};
}
module.exports={loadConfig};
