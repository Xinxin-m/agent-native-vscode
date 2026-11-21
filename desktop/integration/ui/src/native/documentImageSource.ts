/** Render an approved preview URL without replacing the Markdown node's source. */
export function documentImageSource(source:string,assets:Record<string,string>={}):string{
 if(Object.prototype.hasOwnProperty.call(assets,source))return assets[source];
 return /^data:image\/(?:png|jpeg|gif|webp|avif|svg\+xml|bmp|x-icon)[;,]/i.test(source||'')?source:'';
}
