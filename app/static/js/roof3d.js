/* SIGNIX — production rooftop 3D visualizer.
 *
 * Design goals:
 *  - the user's drawn footprint is authoritative;
 *  - AI roof planes/structures are visual guidance, not silent financial changes;
 *  - PV modules use physical dimensions and polygon collision tests;
 *  - the scene reads like a solar-design tool: roof planes, structures, compass,
 *    shadows, orientation, and useful metrics rather than a generic 3D house.
 */
(function () {
  var scene, camera, renderer, rootGroup, animationId;
  var roofPlanesGroup, panelsGroup, structuresGroup, boundaryGroup, contextGroup, shadeOverlayGroup;
  var currentPitch = 0, currentPanels = 0, roofType = 'flat';
  var aiStructuresApplied = false;
  var localRing = null, localObstructions = [], aiObstructions = [], aiPlanes = [];
  var footprintSpan = 12, roofInfo = null;
  var cameraDistance = 14, cameraFitDistance = 14, cameraAzimuth = 0.72, cameraElevation = 0.62;
  var cameraTargetX = 0, cameraTargetY = 2.0, cameraTargetZ = 0;
  var userInteracted = false, aiAnalysis = null, satelliteTexture = null, siteRoofModel = null, siteModelLoading = false;
  var siteSegmentPlanes = [];
  var reconstructedSurfaces = [], reconstructedStructures = [], projectionOrigin = null;
  var selectedSurfaceId = null, viewMode = '3d';
  var shadeGrid = null, shadeSide = null, shadeAvoidedPanels = 0;
  var panelLayoutMode = 'planning_preview';
  var currentPlacedPanels = [];
  var placementRejections = {};
  var currentOrientationDeg = (typeof ORIENTATION_DEG !== 'undefined' && Number.isFinite(Number(ORIENTATION_DEG))) ? Number(ORIENTATION_DEG) : 180;
  var savedOrientationDeg = currentOrientationDeg;
  var savedAnnualGeneration = (typeof ANNUAL_GENERATION !== 'undefined') ? Number(ANNUAL_GENERATION || 0) : 0;
  var orientationPreviewTimer = null, orientationRequestSeq = 0, orientationDirty = false, orientationSaving = false, siteModelRequestSeq = 0;

  var PANEL_W = 1.134, PANEL_D = 1.762, PANEL_GAP = 0.07;
  var EDGE_SETBACK = 0.32, PANEL_THICKNESS = 0.055, FLAT_PANEL_TILT = 10 * Math.PI / 180;
  var RECONSTRUCTED_ROOF_BASE_Y = 0.18;

  function hasWebGL() {
    try { var c = document.createElement('canvas'); return !!(window.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl'))); }
    catch (e) { return false; }
  }
  function viewerCapability(threeAvailable,webglAvailable){
    if(!threeAvailable)return{ok:false,code:'three_unavailable'};
    if(!webglAvailable)return{ok:false,code:'webgl_unavailable'};
    return{ok:true,code:'ready'};
  }
  function reconstructionPayloadState(data){
    if(!data||data.available!==true)return{ok:false,code:'unavailable'};
    var boundary=contractLocalRing(data.authoritative_boundary_local_xz_meters,data.authoritative_boundary_geojson,data.projection_origin?{lng:Number(data.projection_origin.longitude),lat:Number(data.projection_origin.latitude)}:null);
    var surfaces=Array.isArray(data.surfaces)?data.surfaces:[];
    var hasSurface=surfaces.some(function(surface){return surface&&surface.is_valid!==false&&(Array.isArray(surface.polygon_local_xz_meters)&&surface.polygon_local_xz_meters.length>=3||extractRing(surface.polygon_geojson)?.length>=3);});
    if(!boundary||boundary.length<3||!hasSurface)return{ok:false,code:'malformed'};
    return{ok:true,code:'ready'};
  }
  function viewerFailureMessage(code,detail){
    var messages={three_unavailable:'3D preview unavailable because the Three.js renderer did not load. Reload the page and check that local static assets are reachable.',webgl_unavailable:'3D preview unavailable because WebGL is disabled or unsupported on this device.',renderer_initialization_failed:'3D preview could not initialize a WebGL renderer on this device.'};
    return(messages[code]||'3D preview unavailable.')+(detail?' '+detail:'');
  }
  function resolveViewerUnavailable(code,detail){
    var message=viewerFailureMessage(code,detail),fallback=document.getElementById('roof-3d-fallback'),surfaces=document.getElementById('roof3d-surface-list'),structures=document.getElementById('roof3d-structure-list'),satellite=document.getElementById('roof3d-satellite-badge'),shade=document.getElementById('roof3d-shade-badge'),panels=document.getElementById('roof-3d-panel-status'),provider=document.getElementById('roof3d-provider-status'),accept=document.getElementById('roof3d-layout-accept'),acceptance=document.getElementById('roof3d-layout-acceptance-status');
    if(fallback){fallback.textContent=message;fallback.classList.remove('hidden');fallback.style.display='block';}
    if(surfaces)surfaces.textContent='Roof surfaces unavailable because the 3D renderer did not initialize.';
    if(structures)structures.textContent='Structure visualization unavailable because the 3D renderer did not initialize.';
    if(satellite){satellite.textContent='Satellite reference · unavailable';satellite.classList.add('is-error');}
    if(shade){shade.textContent='Shade analysis · unavailable';shade.classList.add('is-error');}
    if(panels)panels.textContent='Physical panel layout unavailable because the 3D renderer did not initialize.';
    if(provider){provider.className='roof3d-provider-status unavailable';provider.textContent='3D renderer unavailable · '+message;}
    if(accept)accept.disabled=true;
    if(acceptance)acceptance.textContent='Layout cannot be accepted until reconstruction and placement complete.';
    return{code:code,message:message};
  }
  function resolveReconstructionFailure(message){
    var surfaces=document.getElementById('roof3d-surface-list'),structures=document.getElementById('roof3d-structure-list'),satellite=document.getElementById('roof3d-satellite-badge'),shade=document.getElementById('roof3d-shade-badge'),status=document.getElementById('roof-ai-status'),accept=document.getElementById('roof3d-layout-accept');
    if(surfaces)surfaces.textContent='Location-specific roof surfaces unavailable; using the authoritative user-drawn boundary.';
    if(structures)structures.textContent='Location-specific structures unavailable.';
    if(satellite){satellite.textContent='Satellite reference · unavailable';satellite.classList.add('is-error');}
    if(shade){shade.textContent='Shade analysis · unavailable';shade.classList.add('is-error');}
    if(status)status.textContent='User-boundary fallback active. '+message;
    if(accept)accept.disabled=true;
  }
  function extractRing(g) {
    if (!g || !g.coordinates) return null;
    var r = g.coordinates[0];
    if (!r || r.length < 3) { if (g.coordinates.length >= 3 && typeof g.coordinates[0][0] === 'number') r = g.coordinates; else return null; }
    return r;
  }
  function geographicOrigin(ring) {
    var valid=(ring||[]).filter(function(p){return p&&p.length>=2&&isFinite(Number(p[0]))&&isFinite(Number(p[1]));});
    if(!valid.length)return null;
    return {lng:valid.reduce(function(sum,p){return sum+Number(p[0]);},0)/valid.length,lat:valid.reduce(function(sum,p){return sum+Number(p[1]);},0)/valid.length};
  }
  function geoJsonRingToLocal(ring, origin) {
    if (!ring || ring.length < 3) return null;
    var pts=[];
    ring.forEach(function(p){
      if (!p || p.length<2) return;
      var lng=Number(p[0]),lat=Number(p[1]); if(!isFinite(lng)||!isFinite(lat))return;
      if(lat>=-90&&lat<=90&&lng>=-180&&lng<=180)pts.push({lat:lat,lng:lng});
    });
    if(pts.length<3)return null;
    var cLat=origin&&Number.isFinite(origin.lat)?origin.lat:0,cLng=origin&&Number.isFinite(origin.lng)?origin.lng:0;
    if(!origin){for(var i=0;i<pts.length;i++){cLat+=pts[i].lat;cLng+=pts[i].lng;} cLat/=pts.length;cLng/=pts.length;}
    var mLat=111320,mLng=111320*Math.cos(cLat*Math.PI/180);
    return pts.map(function(p){return[(p.lng-cLng)*mLng,-(p.lat-cLat)*mLat];});
  }
  function normalizedImagePolygonToLocal(poly,bbox,origin){
    if(!Array.isArray(poly)||poly.length<3||!Array.isArray(bbox)||bbox.length!==4||!origin||!Number.isFinite(Number(origin.lng))||!Number.isFinite(Number(origin.lat)))return null;
    var values=bbox.map(Number),minLng=values[0],minLat=values[1],maxLng=values[2],maxLat=values[3];
    if(!values.every(Number.isFinite)||maxLng<=minLng||maxLat<=minLat)return null;
    var geojson=poly.map(function(point){if(!Array.isArray(point)||point.length<2)return null;var x=Number(point[0]),y=Number(point[1]);if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||x>1000||y<0||y>1000)return null;return[minLng+(x/1000)*(maxLng-minLng),maxLat-(y/1000)*(maxLat-minLat)];});
    if(geojson.some(function(point){return point===null;}))return null;
    return geoJsonRingToLocal(geojson,origin);
  }
  function contractLocalRing(localRingValue,geojson,origin){
    if(Array.isArray(localRingValue)&&localRingValue.length>=3){var valid=localRingValue.every(function(p){return Array.isArray(p)&&p.length>=2&&Number.isFinite(Number(p[0]))&&Number.isFinite(Number(p[1]));});if(valid)return localRingValue.map(function(p){return[Number(p[0]),Number(p[1])];});}
    return geoJsonRingToLocal(extractRing(geojson),origin);
  }
  function ringBounds(r){var minX=Infinity,maxX=-Infinity,minZ=Infinity,maxZ=-Infinity;r.forEach(function(p){minX=Math.min(minX,p[0]);maxX=Math.max(maxX,p[0]);minZ=Math.min(minZ,p[1]);maxZ=Math.max(maxZ,p[1]);});return{minX:minX,maxX:maxX,minZ:minZ,maxZ:maxZ,width:maxX-minX,depth:maxZ-minZ,cx:(minX+maxX)/2,cz:(minZ+maxZ)/2};}
  function polygonCentroid(r){var x=0,z=0,a=0;for(var i=0,j=r.length-1;i<r.length;j=i++){var c=r[j][0]*r[i][1]-r[i][0]*r[j][1];a+=c;x+=(r[j][0]+r[i][0])*c;z+=(r[j][1]+r[i][1])*c;}return Math.abs(a)<1e-8?[r[0][0],r[0][1]]:[x/(3*a),z/(3*a)];}
  function principalAngle(r){var c=polygonCentroid(r),xx=0,zz=0,xz=0;r.forEach(function(p){var dx=p[0]-c[0],dz=p[1]-c[1];xx+=dx*dx;zz+=dz*dz;xz+=dx*dz;});return .5*Math.atan2(2*xz,xx-zz);}
  function rotatePoint(p,a){var c=Math.cos(a),s=Math.sin(a);return[p[0]*c-p[1]*s,p[0]*s+p[1]*c];}
  function transformRing(r,a){return r.map(function(p){return rotatePoint(p,a);});}
  function pointInRing(x,z,r){var inside=false;for(var i=0,j=r.length-1;i<r.length;j=i++){var xi=r[i][0],zi=r[i][1],xj=r[j][0],zj=r[j][1];if((zi>z)!=(zj>z)&&x<((xj-xi)*(z-zi))/((zj-zi)||1e-12)+xi)inside=!inside;}return inside;}
  function pointToRingDistance(point,ring){var minimum=Infinity;for(var i=0;i<ring.length;i++){var start=ring[i],end=ring[(i+1)%ring.length],dx=end[0]-start[0],dz=end[1]-start[1],lengthSquared=dx*dx+dz*dz,fraction=lengthSquared?Math.max(0,Math.min(1,((point[0]-start[0])*dx+(point[1]-start[1])*dz)/lengthSquared)):0,cx=start[0]+fraction*dx,cz=start[1]+fraction*dz;minimum=Math.min(minimum,Math.hypot(point[0]-cx,point[1]-cz));}return minimum;}
  function polygonDistance(a,b){var minimum=Infinity;a.forEach(function(point){minimum=Math.min(minimum,pointToRingDistance(point,b));});b.forEach(function(point){minimum=Math.min(minimum,pointToRingDistance(point,a));});return minimum;}
  function orientation(a,b,c){var v=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);return Math.abs(v)<1e-8?0:(v>0?1:2);}
  function onSegment(a,b,p){return Math.min(a[0],b[0])-1e-7<=p[0]&&p[0]<=Math.max(a[0],b[0])+1e-7&&Math.min(a[1],b[1])-1e-7<=p[1]&&p[1]<=Math.max(a[1],b[1])+1e-7;}
  function segmentsIntersect(a,b,c,d){var o1=orientation(a,b,c),o2=orientation(a,b,d),o3=orientation(c,d,a),o4=orientation(c,d,b);if(o1!==o2&&o3!==o4)return true;return(o1===0&&onSegment(a,b,c))||(o2===0&&onSegment(a,b,d))||(o3===0&&onSegment(c,d,a))||(o4===0&&onSegment(c,d,b));}
  function polygonEdgesIntersect(a,b){for(var i=0;i<a.length;i++)for(var j=0;j<b.length;j++)if(segmentsIntersect(a[i],a[(i+1)%a.length],b[j],b[(j+1)%b.length]))return true;return false;}
  function rect(cx,cz,w,d,a){var hw=w/2,hd=d/2,c=Math.cos(a),s=Math.sin(a);return[[-hw,-hd],[hw,-hd],[hw,hd],[-hw,hd]].map(function(p){return[cx+p[0]*c-p[1]*s,cz+p[0]*s+p[1]*c];});}
  function surfacePanelRect(cx,cz,w,d,a,surface){
    var pitch=(Number(surface&&surface.pitch)||0)*Math.PI/180,az=(Number(surface&&surface.azimuth)||0)*Math.PI/180,tangent=Math.tan(pitch),gx=-tangent*Math.sin(az),gz=tangent*Math.cos(az),ux=Math.cos(a),uz=Math.sin(a),uy=gx*ux+gz*uz,uLength=Math.hypot(ux,uy,uz)||1;ux/=uLength;uy/=uLength;uz/=uLength;var nx=-gx,ny=1,nz=-gz,nLength=Math.hypot(nx,ny,nz)||1;nx/=nLength;ny/=nLength;nz/=nLength;var vx=uy*nz-uz*ny,vy=uz*nx-ux*nz,vz=ux*ny-uy*nx,vLength=Math.hypot(vx,vy,vz)||1;vx/=vLength;vz/=vLength;var hw=w/2,hd=d/2;return[[-1,-1],[1,-1],[1,1],[-1,1]].map(function(sign){return[cx+sign[0]*hw*ux+sign[1]*hd*vx,cz+sign[0]*hw*uz+sign[1]*hd*vz];});
  }
  function polygonsOverlap(a,b){return polygonEdgesIntersect(a,b)||pointInRing(a[0][0],a[0][1],b)||pointInRing(b[0][0],b[0][1],a);}
  function classificationGridValid(grid){return!!(grid&&grid.geometry_compatible!==false&&Array.isArray(grid.classifications)&&Number(grid.rows)>0&&Number(grid.cols)>0&&grid.classifications.length>=Number(grid.rows)*Number(grid.cols)&&Number.isFinite(Number(grid.resolution_meters))&&Number(grid.resolution_meters)>0&&Number.isFinite(Number(grid.origin_x))&&Number.isFinite(Number(grid.origin_z)));}
  function panelIntersectsClassification(poly,grid,kinds){
    var g=grid===undefined?shadeGrid:grid;if(!classificationGridValid(g))return false;
    var wanted=Array.isArray(kinds)?kinds:[kinds],step=Number(g.resolution_meters),ox=Number(g.origin_x),oz=Number(g.origin_z);
    if(!Number.isFinite(step)||step<=0||!Number.isFinite(ox)||!Number.isFinite(oz))return false;
    var b=ringBounds(poly),c0=Math.max(0,Math.floor((b.minX-ox)/step)),c1=Math.min(g.cols-1,Math.floor((b.maxX-ox)/step)),r0=Math.max(0,Math.floor((b.minZ-oz)/step)),r1=Math.min(g.rows-1,Math.floor((b.maxZ-oz)/step));
    for(var r=r0;r<=r1;r++)for(var c=c0;c<=c1;c++){
      var kind=g.classifications[r*g.cols+c];if(wanted.indexOf(kind)<0)continue;
      var x0=ox+c*step,z0=oz+r*step,cell=[[x0,z0],[x0+step,z0],[x0+step,z0+step],[x0,z0+step]];
      if(polygonsOverlap(poly,cell))return true;
    }
    return false;
  }
  function panelTouchesExcludedGrid(poly,grid,layoutMode){
    var g=grid===undefined?shadeGrid:grid,mode=layoutMode||panelLayoutMode||'conservative';
    if(!classificationGridValid(g))return mode!=='planning_preview';
    var excluded=['outside_roof','obstruction','setback','shaded'];
    if(mode!=='planning_preview')excluded.push('unknown');
    return panelIntersectsClassification(poly,g,excluded);
  }
  function panelShadeVerified(poly,grid){return classificationGridValid(grid)&&!panelIntersectsClassification(poly,grid,'unknown');}
  function polygonFits(poly,roof,obstructions,options){options=options||{};if(!roof)return false;var edgeSetback=options.edgeSetback===undefined?EDGE_SETBACK:Number(options.edgeSetback),obstructionSetback=options.obstructionSetback===undefined?.30:Number(options.obstructionSetback);for(var i=0;i<poly.length;i++){if(!pointInRing(poly[i][0],poly[i][1],roof))return false;if(edgeSetback>0&&pointToRingDistance(poly[i],roof)<edgeSetback-1e-7)return false;}if(polygonEdgesIntersect(poly,roof))return false;for(i=0;i<obstructions.length;i++){var o=obstructions[i];if(pointInRing(poly[0][0],poly[0][1],o)||polygonEdgesIntersect(poly,o)||o.some(function(point){return pointInRing(point[0],point[1],poly);}))return false;if(obstructionSetback>0&&polygonDistance(poly,o)<obstructionSetback-1e-7)return false;}if(options.checkGrid!==false&&panelTouchesExcludedGrid(poly,options.grid,options.layoutMode)){return false;}return true;}
  function polygonFitReason(poly,roof,obstructions,options){options=options||{};if(!Array.isArray(poly)||poly.length<3||!Array.isArray(roof)||roof.length<3)return'surface';var edgeSetback=options.edgeSetback===undefined?EDGE_SETBACK:Number(options.edgeSetback),obstructionSetback=options.obstructionSetback===undefined?.30:Number(options.obstructionSetback);for(var i=0;i<poly.length;i++){if(!pointInRing(poly[i][0],poly[i][1],roof))return options.surfaceCheck?'surface':'boundary';if(edgeSetback>0&&pointToRingDistance(poly[i],roof)<edgeSetback-1e-7)return'edge_setback';}if(polygonEdgesIntersect(poly,roof))return options.surfaceCheck?'surface':'boundary';for(i=0;i<(obstructions||[]).length;i++){var obstruction=obstructions[i];if(pointInRing(poly[0][0],poly[0][1],obstruction)||polygonEdgesIntersect(poly,obstruction)||obstruction.some(function(point){return pointInRing(point[0],point[1],poly);}))return'obstruction';if(obstructionSetback>0&&polygonDistance(poly,obstruction)<obstructionSetback-1e-7)return'obstruction_setback';}if(options.checkGrid!==false&&panelTouchesExcludedGrid(poly,options.grid,options.layoutMode))return'classification';return null;}
  function recordPlacementRejection(reason){if(reason)placementRejections[reason]=(placementRejections[reason]||0)+1;}
  function sumSurfacePanelCounts(counts){return Object.values(counts||{}).reduce(function(sum,count){return sum+(Number(count)||0);},0);}
  function layoutCapacityKw(count,moduleWatts){var watts=Number(moduleWatts);return Number.isFinite(watts)?Number(count||0)*watts/1000:null;}
  function layoutDiffersFromEstimate(layoutKw,selectedKw){return layoutKw!==null&&Number.isFinite(Number(selectedKw))&&Math.abs(layoutKw-Number(selectedKw))>.15;}
  function panelLayoutMetrics(panels,moduleWatts){var list=Array.isArray(panels)?panels:[],preview=list.filter(function(panel){return panel.shadeVerified===false;}).length,confirmed=list.filter(function(panel){return panel.shadeVerified===true;}).length;return{total:list.length,preview:preview,confirmed:confirmed,totalKw:layoutCapacityKw(list.length,moduleWatts),previewKw:layoutCapacityKw(preview,moduleWatts),confirmedKw:layoutCapacityKw(confirmed,moduleWatts)};}
  function acceptedLayoutMetrics(accepted){var status=accepted&&accepted.status;if(status!=='accepted')return{status:status==='stale'?'stale':'none',count:null,capacityKw:null};var hasCount=accepted.panel_count!==null&&accepted.panel_count!==undefined&&accepted.panel_count!=='',hasCapacity=accepted.dc_capacity_kw!==null&&accepted.dc_capacity_kw!==undefined&&accepted.dc_capacity_kw!=='',count=Number(accepted.panel_count),capacity=Number(accepted.dc_capacity_kw);return hasCount&&hasCapacity&&Number.isFinite(count)&&count>=0&&Number.isFinite(capacity)&&capacity>=0?{status:'accepted',count:count,capacityKw:capacity}:{status:'invalid',count:null,capacityKw:null};}
  function layoutAcceptanceState(model,panels,grid,mode){if(!model||model.available!==true)return{ok:false,code:'reconstruction_unavailable'};var payload=reconstructionPayloadState(model);if(!payload.ok)return{ok:false,code:'reconstruction_'+payload.code};var revision=model.design_revision;if(!revision||revision.id==null||!Number.isFinite(Number(revision.number)))return{ok:false,code:'design_revision_unavailable'};if(!model.geometry_key||!grid||grid.geometry_key!==model.geometry_key||grid.geometry_compatible===false)return{ok:false,code:'validation_unavailable'};if(mode==='conservative'&&!classificationGridValid(grid))return{ok:false,code:'classification_unavailable'};if(!Array.isArray(panels)||!panels.length)return{ok:false,code:'no_panels'};return{ok:true,code:'ready'};}
  function placementShortfallText(placed,requested,reasons){var missing=Math.max(0,(Number(requested)||0)-(Number(placed)||0));if(!missing)return'';var labels={surface:'surface geometry',boundary:'roof boundary',edge_setback:'edge setback',obstruction:'obstruction',obstruction_setback:'obstruction setback',classification:'shade/classification'},used=Object.keys(reasons||{}).filter(function(key){return Number(reasons[key])>0&&labels[key];}).map(function(key){return labels[key];});return missing+' estimate module'+(missing===1?'':'s')+' could not be placed'+(used.length?' after '+used.join(', ')+' checks.':'.');}
  function cameraFrameForBounds(bounds,aspect,azimuth,elevation){var safeAspect=Math.max(Number(aspect)||1,.35),az=Number.isFinite(Number(azimuth))?Number(azimuth):.72,el=Number.isFinite(Number(elevation))?Number(elevation):.55,width=Math.max(Number(bounds.width)||0,.5),depth=Math.max(Number(bounds.depth)||0,.5),height=Math.max(Number(bounds.height)||0,.5),projectedWidth=Math.abs(Math.sin(az))*width+Math.abs(Math.cos(az))*depth,projectedHeight=Math.abs(Math.cos(az)*Math.sin(el))*width+Math.abs(Math.sin(az)*Math.sin(el))*depth+Math.abs(Math.cos(el))*height;return Math.max(5.5,Math.max(projectedWidth/safeAspect,projectedHeight)*1.18);}
  function cameraFrameForRing(ring,aspect,azimuth,elevation,height){if(!Array.isArray(ring)||ring.length<3)return null;var bounds=ringBounds(ring);bounds.height=Math.max(Number(height)||.5,.5);return cameraFrameForBounds(bounds,aspect,azimuth,elevation);}
  function clearGroup(g){if(!g)return;while(g.children.length){var c=g.children.pop();if(c.geometry)c.geometry.dispose();if(c.material){if(Array.isArray(c.material))c.material.forEach(function(m){m.dispose();});else c.material.dispose();}}}
  function resetViewerGroups(){clearGroup(rootGroup);roofPlanesGroup=new THREE.Group();roofPlanesGroup.name='roof-planes';panelsGroup=new THREE.Group();panelsGroup.name='panels';structuresGroup=new THREE.Group();structuresGroup.name='structures';boundaryGroup=new THREE.Group();boundaryGroup.name='boundary';contextGroup=new THREE.Group();contextGroup.name='context';shadeOverlayGroup=new THREE.Group();shadeOverlayGroup.name='shade-overlay';rootGroup.add(contextGroup,roofPlanesGroup,shadeOverlayGroup,structuresGroup,panelsGroup,boundaryGroup);}
  function shapeFromRing(r){var s=new THREE.Shape();s.moveTo(r[0][0],r[0][1]);for(var i=1;i<r.length;i++)s.lineTo(r[i][0],r[i][1]);s.closePath();return s;}

  function surfaceHeight(surface,x,z){
    var pitch=(Number(surface.pitch)||0)*Math.PI/180,az=(Number(surface.azimuth)||0)*Math.PI/180;
    var down=[Math.sin(az),-Math.cos(az)],ref=surface.reference||[0,0];
    return RECONSTRUCTED_ROOF_BASE_Y+(Number(surface.relativeHeight)||0)+(Number(surface.verticalOffset)||0)-Math.tan(pitch)*((x-ref[0])*down[0]+(z-ref[1])*down[1]);
  }
  function surfaceAt(x,z){
    for(var i=0;i<reconstructedSurfaces.length;i++)if(pointInRing(x,z,reconstructedSurfaces[i].polygon))return reconstructedSurfaces[i];
    return null;
  }
  function roofHeightFromSurfaces(x,z){var surface=surfaceAt(x,z);return surface?surfaceHeight(surface,x,z):RECONSTRUCTED_ROOF_BASE_Y;}
  function makeSurfaceMesh(surface,index){
    var poly=surface.polygon,pts=poly.map(function(p){return new THREE.Vector2(p[0],p[1]);});
    var tris=THREE.ShapeUtils.triangulateShape(pts,[]),pos=[];
    tris.forEach(function(t){t.forEach(function(i){var p=poly[i];pos.push(p[0],surfaceHeight(surface,p[0],p[1]),p[1]);});});
    if(!pos.length)return null;
    var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));geom.computeVertexNormals();
    var colors=[0x8d9698,0x819095,0x929698,0x79898d];
    var mat=new THREE.MeshStandardMaterial({color:colors[index%colors.length],roughness:.78,metalness:.025,side:THREE.DoubleSide});
    var mesh=new THREE.Mesh(geom,mat);mesh.castShadow=true;mesh.receiveShadow=true;mesh.userData={kind:'roof-surface',surfaceId:surface.parentId||surface.id,baseColor:mat.color.getHex()};roofPlanesGroup.add(mesh);
    return mesh;
  }
  function addSurfaceEdges(){
    var edges={};
    function pointKey(point){return point[0].toFixed(3)+','+point[1].toFixed(3);}
    reconstructedSurfaces.forEach(function(surface){surface.polygon.forEach(function(start,index){var end=surface.polygon[(index+1)%surface.polygon.length],a=pointKey(start),b=pointKey(end),key=a<b?a+'|'+b:b+'|'+a;if(!edges[key])edges[key]={start:start,end:end,surface:surface,count:0,parents:{}};edges[key].count++;edges[key].parents[surface.parentId||surface.id]=true;});});
    var positions=[];Object.values(edges).forEach(function(edge){var parentCount=Object.keys(edge.parents).length;if(edge.count>1&&parentCount===1)return;[edge.start,edge.end].forEach(function(point){positions.push(point[0],surfaceHeight(edge.surface,point[0],point[1])+.024,point[1]);});});
    if(!positions.length)return;var geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));var line=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:0x4f5d61,transparent:true,opacity:.86}));line.userData={kind:'roof-edge'};roofPlanesGroup.add(line);
  }
  function addAuthoritativeBoundary(){
    var points=localRing.map(function(p){return new THREE.Vector3(p[0],roofHeightFromSurfaces(p[0],p[1])+.11,p[1]);});points.push(points[0].clone());
    var line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:0xf2a33a,linewidth:2,transparent:true,opacity:.98,depthTest:false}));line.renderOrder=20;line.userData={kind:'authoritative-boundary'};boundaryGroup.add(line);
  }
  function addReconstructedStructures(){
    reconstructedStructures.forEach(function(item,index){
      var r=item.polygon;if(!r||r.length<3)return;var measuredHeight=Number(item.height),heightKnown=Number.isFinite(measuredHeight)&&measuredHeight>0,height=heightKnown?measuredHeight:0,points=r.map(function(point){return new THREE.Vector2(point[0],point[1]);}),triangles=THREE.ShapeUtils.triangulateShape(points,[]),positions=[];
      function pushVertex(point,offset){positions.push(point[0],roofHeightFromSurfaces(point[0],point[1])+.025+offset,point[1]);}
      triangles.forEach(function(triangle){pushVertex(r[triangle[0]],height);pushVertex(r[triangle[1]],height);pushVertex(r[triangle[2]],height);if(heightKnown){pushVertex(r[triangle[2]],0);pushVertex(r[triangle[1]],0);pushVertex(r[triangle[0]],0);}});
      if(heightKnown)r.forEach(function(start,edgeIndex){var end=r[(edgeIndex+1)%r.length];pushVertex(start,0);pushVertex(end,0);pushVertex(end,height);pushVertex(start,0);pushVertex(end,height);pushVertex(start,height);});
      var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geom.computeVertexNormals();
      var mesh=new THREE.Mesh(geom,new THREE.MeshStandardMaterial({color:heightKnown?(index%2?0x9da5a7:0xadb4b5):0xc58b3a,roughness:.84,transparent:!heightKnown,opacity:heightKnown?1:.42}));mesh.castShadow=heightKnown;mesh.receiveShadow=heightKnown;mesh.userData={kind:'structure',structureId:item.id,heightKnown:heightKnown};structuresGroup.add(mesh);
    });
  }
  function packReconstructedPanels(requested){
    var candidates=[],angle=azimuthToWorldAngle(currentOrientationDeg),obstructions=reconstructedStructures.map(function(s){return s.polygon;});
    reconstructedSurfaces.forEach(function(surface){
      if(surface.valid===false||!surface.polygon||surface.polygon.length<3)return;
      var best=[];
      [{w:PANEL_W,d:PANEL_D},{w:PANEL_D,d:PANEL_W}].forEach(function(size){
        var rotated=transformRing(surface.polygon,-angle),bounds=ringBounds(rotated),found=[];
        for(var z=bounds.minZ+EDGE_SETBACK+size.d/2;z<=bounds.maxZ-EDGE_SETBACK-size.d/2+.001;z+=size.d+PANEL_GAP){
          for(var x=bounds.minX+EDGE_SETBACK+size.w/2;x<=bounds.maxX-EDGE_SETBACK-size.w/2+.001;x+=size.w+PANEL_GAP){
            var center=rotatePoint([x,z],angle),corners=surfacePanelRect(center[0],center[1],size.w,size.d,angle,surface);
            var surfaceReason=polygonFitReason(corners,surface.polygon,obstructions,{edgeSetback:0,obstructionSetback:0,checkGrid:false,surfaceCheck:true});
            if(surfaceReason){recordPlacementRejection(surfaceReason);continue;}
            var roofReason=polygonFitReason(corners,localRing,obstructions,{grid:shadeGrid,layoutMode:panelLayoutMode});
            if(roofReason){recordPlacementRejection(roofReason);continue;}
            found.push({x:center[0],z:center[1],w:size.w,d:size.d,angle:angle,corners:corners,surface:surface,shadeVerified:panelShadeVerified(corners,shadeGrid)});
          }
        }
        if(found.length>best.length)best=found;
      });
      candidates=candidates.concat(best);
    });
    return selectNonOverlappingPanels(candidates,requested);
  }
  function selectNonOverlappingPanels(candidates,requested){var accepted=[];(candidates||[]).forEach(function(candidate){if(!accepted.some(function(existing){return polygonsOverlap(candidate.corners,existing.corners);}))accepted.push(candidate);});return accepted.slice(0,Math.max(0,Math.min(160,Number(requested)||0)));}
  function addReconstructedPanels(requested){
    shadeAvoidedPanels=0;placementRejections={};var panels=packReconstructedPanels(requested);shadeAvoidedPanels=placementRejections.classification||0;
    var verifiedMat=new THREE.MeshStandardMaterial({color:0x114d82,roughness:.25,metalness:.3,side:THREE.DoubleSide}),previewMat=new THREE.MeshStandardMaterial({color:0x315f73,roughness:.34,metalness:.18,side:THREE.DoubleSide});
    var verifiedEdge=new THREE.LineBasicMaterial({color:0xc9e0ea,transparent:true,opacity:.82}),previewEdge=new THREE.LineBasicMaterial({color:0xf0ad3d,transparent:true,opacity:1});
    panels.forEach(function(panel){
      var vertices=[];panel.corners.forEach(function(p){vertices.push(new THREE.Vector3(p[0],surfaceHeight(panel.surface,p[0],p[1])+.085,p[1]));});
      var geom=new THREE.BufferGeometry().setFromPoints(vertices);geom.setIndex([0,1,2,0,2,3]);geom.computeVertexNormals();var mesh=new THREE.Mesh(geom,panel.shadeVerified?verifiedMat:previewMat);mesh.castShadow=true;mesh.userData={kind:'panel',surfaceId:panel.surface.parentId||panel.surface.id,shadeVerified:panel.shadeVerified};panelsGroup.add(mesh);
      var outline=vertices.map(function(v){return v.clone();});outline.push(outline[0].clone());panelsGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(outline),panel.shadeVerified?verifiedEdge:previewEdge));
    });
    currentPlacedPanels=panels;updatePanelStatus(panels,requested);updateSurfacePanelCounts(panels);syncLayoutAcceptance();return panels.length;
  }
  function updateSurfacePanelCounts(panels){
    var counts={};panels.forEach(function(panel){var id=panel.surface.parentId||panel.surface.id;counts[id]=(counts[id]||0)+1;});
    reconstructedSurfaces.forEach(function(surface){surface.panelCount=counts[surface.parentId||surface.id]||0;});var total=sumSurfacePanelCounts(counts),headline=document.getElementById('roof3d-module-count');if(headline)headline.innerHTML=String(total)+' <small>modules</small>';renderInspector();
  }
  function renderInspector(){
    var list=document.getElementById('roof3d-surface-list'),structures=document.getElementById('roof3d-structure-list');if(!list||!structures)return;
    var groups={};reconstructedSurfaces.forEach(function(s){var id=s.parentId||s.id;if(!groups[id])groups[id]={id:id,area:0,usableArea:0,pitch:s.pitch,azimuth:s.azimuth,elevation:s.referenceHeight,elevationDatum:s.elevationDatum,source:s.source,confidence:s.confidence,geometryKind:s.geometryKind,panels:0,valid:s.valid!==false};groups[id].area+=Number(s.area)||0;groups[id].usableArea+=Number(s.usableArea)||0;groups[id].panels=Math.max(groups[id].panels,Number(s.panelCount)||0);groups[id].valid=groups[id].valid&&s.valid!==false;});
    var items=Object.values(groups);list.innerHTML=items.length?items.map(function(s,index){var elevation=s.elevation==null?'Elevation unavailable':Number(s.elevation).toFixed(1)+(s.elevationDatum==='mean_sea_level'?' m AMSL':s.elevationDatum==='relative_building_height'?' m building height':' m elevation · datum unavailable'),geometry=String(s.geometryKind||'geometry unavailable').replaceAll('_',' '),validity=s.valid?'':' · invalid surface';return '<button type="button" class="roof3d-surface-item'+(selectedSurfaceId===s.id?' active':'')+'" data-surface-id="'+s.id+'"><strong>Surface '+(index+1)+'</strong><span>'+s.area.toFixed(1)+' m² area · '+s.usableArea.toFixed(1)+' m² usable</span><span>'+Math.round(s.pitch)+'° pitch · '+Math.round(s.azimuth)+'° azimuth</span><small>'+s.panels+' modules · '+elevation+' · '+geometry+' · '+String(s.source||'unavailable').replaceAll('_',' ')+' · '+String(s.confidence||'unavailable')+validity+'</small></button>';}).join(''):'<p class="roof3d-empty">Roof surface data unavailable</p>';
    structures.innerHTML=reconstructedStructures.length?reconstructedStructures.map(function(s){var bounds=ringBounds(s.polygon);return '<div class="roof3d-structure-item"><strong>'+String(s.type||'Structure')+'</strong><span>'+(s.height==null?'Height unavailable · flat display marker only':Number(s.height).toFixed(1)+' m high')+' · '+bounds.width.toFixed(1)+' × '+bounds.depth.toFixed(1)+' m footprint</span><small>'+(s.isObstruction?'Panel obstruction · ':'')+String(s.source||'unavailable')+' · '+String(s.confidence||'unavailable')+'</small></div>';}).join(''):'<p class="roof3d-empty">No reconstructed structures</p>';
    list.querySelectorAll('[data-surface-id]').forEach(function(button){button.addEventListener('click',function(){selectSurface(this.dataset.surfaceId);});});
  }
  function selectSurface(id){selectedSurfaceId=id;roofPlanesGroup.children.forEach(function(child){if(child.material&&child.userData.kind==='roof-surface'){var active=(child.userData.surfaceId===id);child.material.color.setHex(active?0xc68b43:child.userData.baseColor);child.material.emissive?.setHex(active?0x2b1704:0x000000);}});renderInspector();}

  function buildReconstructedModel(panelCount){
    if(!siteRoofModel||!Array.isArray(siteRoofModel.surfaces))return false;resetViewerGroups();
    var originData=siteRoofModel.projection_origin||{},origin={lng:Number(originData.longitude),lat:Number(originData.latitude)};projectionOrigin=origin;
    localRing=contractLocalRing(siteRoofModel.authoritative_boundary_local_xz_meters,siteRoofModel.authoritative_boundary_geojson,origin)||localRing;
    if(!localRing||localRing.length<3){reconstructedSurfaces=[];reconstructedStructures=[];currentPlacedPanels=[];setGeometryAvailability(false);updatePanelStatus([],panelCount);updateProviderStatus();return false;}
    var rawSurfaces=siteRoofModel.surfaces||[],heights=rawSurfaces.filter(function(s){return s.reference_height_meters!=null&&Number.isFinite(Number(s.reference_height_meters));}).map(function(s){return Number(s.reference_height_meters);}),minHeight=heights.length?Math.min.apply(null,heights):null;
    reconstructedSurfaces=rawSurfaces.map(function(s,index){var ref=s.reference_point||{},mLat=111320,mLng=mLat*Math.cos(origin.lat*Math.PI/180),providedReference=s.reference_local_xz_meters,reference=Array.isArray(providedReference)&&providedReference.length>=2?[Number(providedReference[0]),Number(providedReference[1])]:[(Number(ref.longitude)-origin.lng)*mLng,-(Number(ref.latitude)-origin.lat)*mLat],knownHeight=s.reference_height_meters!=null&&Number.isFinite(Number(s.reference_height_meters));if(!Number.isFinite(reference[0])||!Number.isFinite(reference[1]))reference=[0,0];return{id:s.id||('surface-'+index),parentId:s.parent_surface_id||s.id,polygon:contractLocalRing(s.polygon_local_xz_meters,s.polygon_geojson,origin),pitch:Number(s.pitch_degrees)||0,azimuth:Number(s.azimuth_degrees)||0,normal:s.plane_normal_enu,reference:reference,referenceHeight:knownHeight?Number(s.reference_height_meters):null,elevationDatum:s.elevation_datum||null,relativeHeight:knownHeight&&minHeight!=null?Math.max(0,Math.min(8,Number(s.reference_height_meters)-minHeight)):0,verticalOffset:0,area:Number(s.area_sqm)||0,projectedArea:Number(s.projected_area_sqm)||0,usableArea:Number(s.usable_area_sqm)||0,valid:s.is_valid!==false,source:s.source,confidence:s.confidence,geometryKind:s.geometry_kind,limitations:s.limitations||[]};}).filter(function(s){return s.polygon&&s.polygon.length>=3;});
    var minimumPlaneOffset=Infinity;
    reconstructedSurfaces.forEach(function(surface){var pitch=surface.pitch*Math.PI/180,az=surface.azimuth*Math.PI/180,down=[Math.sin(az),-Math.cos(az)];surface.polygon.forEach(function(point){var offset=surface.relativeHeight-Math.tan(pitch)*((point[0]-surface.reference[0])*down[0]+(point[1]-surface.reference[1])*down[1]);minimumPlaneOffset=Math.min(minimumPlaneOffset,offset);});});
    var surfaceLift=Number.isFinite(minimumPlaneOffset)&&minimumPlaneOffset<0?-minimumPlaneOffset:0;reconstructedSurfaces.forEach(function(surface){surface.verticalOffset=surfaceLift;});
    reconstructedStructures=(siteRoofModel.structures||[]).map(function(s){return{id:s.id,type:s.type,polygon:contractLocalRing(s.footprint_local_xz_meters,s.footprint_geojson,origin),height:s.height_meters,renderHeight:s.render_height_meters,area:s.footprint_area_sqm,isObstruction:s.is_obstruction!==false,source:s.source,confidence:s.confidence};}).filter(function(s){return s.polygon&&s.polygon.length>=3;});
    localObstructions=reconstructedStructures.map(function(s){return s.polygon;});var bounds=ringBounds(localRing);footprintSpan=Math.max(Math.max(bounds.width,bounds.depth),7);var top=RECONSTRUCTED_ROOF_BASE_Y;reconstructedSurfaces.forEach(function(surface){surface.polygon.forEach(function(point){top=Math.max(top,surfaceHeight(surface,point[0],point[1]));});});roofInfo={type:'reconstructed',baseY:RECONSTRUCTED_ROOF_BASE_Y,rise:Math.max(0,top-RECONSTRUCTED_ROOF_BASE_Y)};
    setGeometryAvailability(true);addGround();reconstructedSurfaces.filter(function(surface){return surface.valid!==false;}).forEach(makeSurfaceMesh);addSurfaceEdges();addClassificationOverlay();addReconstructedStructures();addReconstructedPanels(panelCount);addAuthoritativeBoundary();renderInspector();applyViewPresentation();updateProviderStatus();updateShadeStatus();updateOrientation(roofInfo);fitCamera(userInteracted||viewMode!=='3d');return true;
  }

  function siteHeightAt(x, z) {
    var g = siteRoofModel && siteRoofModel.dsmGrid;
    if (!g || !g.x || !g.z || !g.heights || !g.rows || !g.cols) return null;
    var xs=g.x, zs=g.z, rows=g.rows, cols=g.cols;
    if (x<Math.min(xs[0],xs[cols-1]) || x>Math.max(xs[0],xs[cols-1]) || z<Math.min(zs[0],zs[rows-1]) || z>Math.max(zs[0],zs[rows-1])) return null;
    function locate(arr,v){
      var asc=arr[arr.length-1]>=arr[0], lo=0, hi=arr.length-1;
      if(!asc){ v=-v; arr=arr.map(function(n){return -n;}); }
      while(lo<hi){var mid=Math.floor((lo+hi)/2); if(arr[mid]<v)lo=mid+1;else hi=mid;}
      var i=Math.max(1,Math.min(arr.length-1,lo)); return [i-1,i];
    }
    var xi=locate(xs,x), zi=locate(zs,z), x0=xs[xi[0]], x1=xs[xi[1]], z0=zs[zi[0]], z1=zs[zi[1]];
    var tx=(x1-x0)!==0?(x-x0)/(x1-x0):0, tz=(z1-z0)!==0?(z-z0)/(z1-z0):0;
    function val(r,c){var v=g.heights[r*cols+c]; return Number.isFinite(Number(v))?Number(v):null;}
    var a=val(zi[0],xi[0]), b=val(zi[0],xi[1]), c=val(zi[1],xi[0]), d=val(zi[1],xi[1]);
    if([a,b,c,d].some(function(v){return v===null;})) return null;
    return a*(1-tx)*(1-tz)+b*tx*(1-tz)+c*(1-tx)*tz+d*tx*tz;
  }
  function buildSiteRoofMesh(){
    var g=siteRoofModel && siteRoofModel.dsmGrid; if(!g)return null;
    var positions=[], normals=[]; var rows=g.rows, cols=g.cols, xs=g.x, zs=g.z, h=g.heights;
    function valid(r,c){var v=h[r*cols+c];return Number.isFinite(Number(v));}
    for(var r=0;r<rows-1;r++) for(var c=0;c<cols-1;c++){
      if(!valid(r,c)||!valid(r,c+1)||!valid(r+1,c)||!valid(r+1,c+1)) continue;
      var p00=[xs[c],Number(h[r*cols+c]),zs[r]], p10=[xs[c+1],Number(h[r*cols+c+1]),zs[r]], p01=[xs[c],Number(h[(r+1)*cols+c]),zs[r+1]], p11=[xs[c+1],Number(h[(r+1)*cols+c+1]),zs[r+1]];
      var cx=(p00[0]+p10[0]+p01[0]+p11[0])/4, cz=(p00[2]+p10[2]+p01[2]+p11[2])/4;
      if(!pointInRing(cx,cz,localRing)) continue;
      var tri=[[p00,p10,p11],[p00,p11,p01]];
      tri.forEach(function(t){t.forEach(function(p){positions.push(p[0],p[1]+1.8,p[2]);});});
    }
    if(!positions.length)return null;
    var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geom.computeVertexNormals();
    var mat=new THREE.MeshStandardMaterial({color:0x8d9294,roughness:.78,metalness:.02,side:THREE.DoubleSide});
    var mesh=new THREE.Mesh(geom,mat);mesh.castShadow=true;mesh.receiveShadow=true;(roofPlanesGroup||rootGroup).add(mesh);return mesh;
  }
  function siteSurfaceSlope(x,z){
    var e=.22, a=siteHeightAt(x-e,z), b=siteHeightAt(x+e,z), c=siteHeightAt(x,z-e), d=siteHeightAt(x,z+e);
    if([a,b,c,d].some(function(v){return v===null;})) return {pitch:0,angle:0};
    var gx=(b-a)/(2*e), gz=(d-c)/(2*e), mag=Math.hypot(gx,gz); if(mag<.0001)return {pitch:0,angle:0};
    return {pitch:Math.atan(mag),angle:Math.atan2(-gx,-gz)};
  }
  function buildSiteRoofModel(panelCount){
    if(!siteRoofModel)return false;
    if(Array.isArray(siteRoofModel.surfaces))return buildReconstructedModel(panelCount);
    resetViewerGroups();
    var g=siteRoofModel.dsmGrid, b=ringBounds(localRing); footprintSpan=Math.max(Math.min(Math.max(b.width,b.depth),30),7);
    roofInfo={type:'site',baseY:1.8,angle:0,pitchRad:0,halfD:Math.max(b.depth/2,.5),halfW:Math.max(b.width/2,.5),centerZ:b.cz,centerX:b.cx,minZ:b.minZ,depth:Math.max(b.depth,.5),rise:g?Math.max(Number(g.maxRelativeHeightMeters)||0,0):2.2};
    addGround();
    if(g){
      buildSiteRoofMesh();
    } else {
      // Building Insights fallback: use Google's roof-segment bounding boxes and
      // pitch/azimuth metadata when the DSM Data Layer is not covered/available.
      siteSegmentPlanes=[];
      (siteRoofModel.roof&&siteRoofModel.roof.segments||[]).forEach(function(seg){
        var fp=seg.footprint; if(!fp||fp.length<3)return;
        var poly=geoJsonRingToLocal(fp); if(!poly||poly.length<3)return;
        // Keep only the part overlapping the user's selected roof visually.
        var c=polygonCentroid(poly); if(!pointInRing(c[0],c[1],localRing))return;
        siteSegmentPlanes.push({polygon:poly,pitch:Number(seg.pitchDegrees)||0,azimuth:Number(seg.azimuthDegrees)||0,height:Number(seg.planeHeightAtCenterMeters)||0});
      });
      if(siteSegmentPlanes.length){
        siteSegmentPlanes.forEach(function(p){
          var poly=p.polygon, pitch=(p.pitch||0)*Math.PI/180, az=(p.azimuth||0)*Math.PI/180;
          var c=polygonCentroid(poly), down=[Math.sin(az),-Math.cos(az)];
          var pts=poly.map(function(q){var d=(q[0]-c[0])*down[0]+(q[1]-c[1])*down[1];return new THREE.Vector3(q[0],roofInfo.baseY+(p.height?0.03:0)+Math.tan(pitch)*d,q[1]);});
          var pos=[];for(var i=1;i<pts.length-1;i++)pos.push(pts[0].x,pts[0].y,pts[0].z,pts[i].x,pts[i].y,pts[i].z,pts[i+1].x,pts[i+1].y,pts[i+1].z);
          var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));geom.computeVertexNormals();
          var mat=new THREE.MeshStandardMaterial({color:0x8d9294,roughness:.78,metalness:.02,side:THREE.DoubleSide});
           var mesh=new THREE.Mesh(geom,mat);mesh.castShadow=true;mesh.receiveShadow=true;(roofPlanesGroup||rootGroup).add(mesh);
        });
      }
    }
    addShadeOverlay();
    addStructureBlocks(localObstructions.map(function(r){return{polygon:r};}),'user');
    addStructureBlocks(aiObstructions,'ai');
    addPanels(panelCount,roofInfo);applyViewPresentation();updateOrientation(roofInfo);fitCamera(userInteracted||viewMode!=='3d');return true;
  }

  function updateProviderStatus(){
    var badge=document.getElementById('roof3d-provider-status');if(!badge||!siteRoofModel)return;var status=siteRoofModel.status_badge||{};
    badge.className='roof3d-provider-status '+(status.tone||'fallback');badge.innerHTML='<strong>'+String(status.label||'Rooftop model')+'</strong><span>'+String(status.detail||'')+'</span>';
    var satellite=document.querySelector('[data-roof3d-view="satellite"]');if(satellite){var available=Boolean(siteRoofModel.imagery&&siteRoofModel.imagery.available);satellite.disabled=!available;satellite.title=available?'Show available satellite reference':'Satellite imagery unavailable for this reconstruction';}
    var satelliteBadge=document.getElementById('roof3d-satellite-badge');if(satelliteBadge&&!siteRoofModel.imagery?.available){satelliteBadge.textContent='Satellite reference · unavailable';satelliteBadge.classList.add('is-error');satelliteBadge.classList.remove('is-ready');}
  }
  function applyLayerVisibility(){
    [['roof-planes',roofPlanesGroup],['shade-overlay',shadeOverlayGroup],['panels',panelsGroup],['structures',structuresGroup],['boundary',boundaryGroup],['context',contextGroup]].forEach(function(pair){var input=document.querySelector('[data-roof3d-layer="'+pair[0]+'"]');if(pair[1])pair[1].visible=(!input||input.checked)&&!(viewMode==='wireframe'&&pair[0]==='panels');});
  }
  function applyViewPresentation(){
    document.querySelectorAll('[data-roof3d-view]').forEach(function(button){var active=button.dataset.roof3dView===viewMode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    if(roofPlanesGroup)roofPlanesGroup.traverse(function(obj){if(obj.material&&obj.userData.kind==='roof-surface'){obj.material.wireframe=false;obj.material.transparent=viewMode==='wireframe';obj.material.opacity=viewMode==='wireframe'?.12:1;obj.visible=true;}if(obj.userData.kind==='roof-edge')obj.visible=true;});
    if(structuresGroup)structuresGroup.traverse(function(obj){if(obj.material)obj.material.wireframe=viewMode==='wireframe';});
    applyLayerVisibility();
  }
  function setViewMode(mode){
    if(mode==='satellite'&&!(siteRoofModel&&siteRoofModel.imagery&&siteRoofModel.imagery.available))return;
    viewMode=['3d','top','wireframe','satellite'].indexOf(mode)>=0?mode:'3d';
    if(viewMode==='top'){cameraElevation=1.555;cameraAzimuth=0;}else if(viewMode!=='wireframe'){cameraElevation=.55;cameraAzimuth=.72;}
    applyViewPresentation();
    fitCamera(true);
  }
  function setPanelLayoutMode(mode){
    panelLayoutMode=mode==='conservative'?'conservative':'planning_preview';
    var control=document.getElementById('roof3d-layout-mode');if(control)control.value=panelLayoutMode;
    updateShadeStatus();
    if(siteRoofModel)buildRoofModel(currentPitch,currentPanels);
  }
  function syncOrientationApplyButton(applyButton,isDirty){
    if(applyButton)applyButton.disabled=!isDirty||orientationSaving;
    return Boolean(isDirty);
  }
  function syncLayoutAcceptance(){var button=document.getElementById('roof3d-layout-accept'),state=layoutAcceptanceState(siteRoofModel,currentPlacedPanels,shadeGrid,panelLayoutMode);if(button){button.disabled=!state.ok;button.title=state.ok?'Accept this validated visible layout':state.code==='no_panels'?'No valid modules are available to accept.':'Layout acceptance requires a current reconstruction and compatible validation data.';}return state;}
  function bindPlannerControls(){
    document.querySelectorAll('[data-roof3d-layer]').forEach(function(input){input.addEventListener('change',applyLayerVisibility);});
    document.querySelectorAll('[data-roof3d-view]').forEach(function(button){button.addEventListener('click',function(){setViewMode(this.dataset.roof3dView);});});
    document.getElementById('roof3d-reset-view')?.addEventListener('click',function(){userInteracted=false;setViewMode('3d');fitCamera();});
    var azimuth=document.getElementById('roof3d-panel-azimuth');if(azimuth){azimuth.value=String(Math.round(currentOrientationDeg));azimuth.addEventListener('change',function(){setOrientation(Number(this.value),true);});}
    var layoutMode=document.getElementById('roof3d-layout-mode');if(layoutMode){layoutMode.value=panelLayoutMode;layoutMode.addEventListener('change',function(){setPanelLayoutMode(this.value);});}
    document.getElementById('roof3d-layout-accept')?.addEventListener('click',acceptVisibleLayout);
  }
  async function acceptVisibleLayout(){
    var button=document.getElementById('roof3d-layout-accept'),status=document.getElementById('roof3d-layout-acceptance-status'),revision=siteRoofModel&&siteRoofModel.design_revision,locationId=typeof SIGNIX_LOCATION_ID!=='undefined'?SIGNIX_LOCATION_ID:null;
    var readiness=layoutAcceptanceState(siteRoofModel,currentPlacedPanels,shadeGrid,panelLayoutMode);
    if(!button||!status||!locationId||!readiness.ok){if(status)status.textContent='A current reconstruction and compatible validation data are required before accepting this layout.';syncLayoutAcceptance();return;}
    button.disabled=true;status.textContent='Validating layout and recalculating the saved estimate…';
    var payload={design_revision_id:revision.id,design_revision_number:revision.number,geometry_key:siteRoofModel.geometry_key,layout_mode:panelLayoutMode,panels:currentPlacedPanels.map(function(panel){return{surface_id:panel.surface.id,corners_local_xz_meters:panel.corners.map(function(point){return[Number(point[0].toFixed(6)),Number(point[1].toFixed(6))];})};})};
    try{var csrf=document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')||'',res=await fetch('/api/locations/'+encodeURIComponent(locationId)+'/rooftop-layout/accept',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf,'X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(payload)}),data=await res.json();if(!res.ok)throw new Error(data.error||'Layout acceptance failed');siteRoofModel.layout_acceptance=data.accepted_layout;siteRoofModel.design_revision={id:data.accepted_layout.accepted_design_revision_id,number:data.accepted_layout.accepted_design_revision_number};updateAcceptedLayoutStatus(data.accepted_layout);status.textContent='Accepted · '+data.accepted_layout.panel_count+' modules · '+Number(data.accepted_layout.dc_capacity_kw).toFixed(2)+' kWp. Refreshing authoritative report values…';button.textContent='Layout accepted';setTimeout(function(){window.location.reload();},700);}
    catch(error){status.textContent=error.message;syncLayoutAcceptance();}
  }
  function bindSurfaceSelection(container){
    var raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();container.addEventListener('click',function(event){if(!renderer||!camera||!roofPlanesGroup)return;var rect=renderer.domElement.getBoundingClientRect();pointer.x=((event.clientX-rect.left)/rect.width)*2-1;pointer.y=-((event.clientY-rect.top)/rect.height)*2+1;raycaster.setFromCamera(pointer,camera);var hit=raycaster.intersectObjects(roofPlanesGroup.children,false).find(function(item){return item.object.userData.kind==='roof-surface';});if(hit)selectSurface(hit.object.userData.surfaceId);});
  }

  function roofLocalPoint(x, z, info) {
    var q = rotatePoint([x, z], -info.angle);
    return { x: q[0], z: q[1] };
  }

  function roofHeight(x, z, info) {
    if (info && info.type === 'site') { var sh=siteHeightAt(x,z); return info.baseY + (sh===null?0:sh); }
    if (!info || info.type === 'flat') return info ? info.baseY : 0;
    var q = roofLocalPoint(x, z, info);
    var nx = Math.max(-1, Math.min(1, (q.x - info.centerX) / Math.max(info.halfW, 0.001)));
    var nz = Math.max(-1, Math.min(1, (q.z - info.centerZ) / Math.max(info.halfD, 0.001)));
    if (info.type === 'shed') {
      var t = Math.max(0, Math.min(1, (q.z - info.minZ) / Math.max(info.depth, 0.001)));
      return info.baseY + info.rise * (1 - t);
    }
    if (info.type === 'hip') {
      var edge = Math.max(Math.abs(nx), Math.abs(nz));
      return info.baseY + info.rise * (1 - Math.min(1, edge));
    }
    // Gable: ridge runs along local X; both sides meet at the centre.
    return info.baseY + info.rise * (1 - Math.abs(nz));
  }

  function roofSurfaceSlope(x, z, info) {
    if (info && info.type === 'site') return siteSurfaceSlope(x,z);
    if (!info || info.type === 'flat') return { pitch: FLAT_PANEL_TILT, angle: info ? info.angle : 0 };
    // Estimate the local roof gradient from the same height function used to
    // draw the roof. This keeps every module tangent to the rendered surface.
    var eps = 0.18;
    var hx1 = roofHeight(x + eps, z, info), hx0 = roofHeight(x - eps, z, info);
    var hz1 = roofHeight(x, z + eps, info), hz0 = roofHeight(x, z - eps, info);
    var gx = (hx1 - hx0) / (2 * eps);
    var gz = (hz1 - hz0) / (2 * eps);
    var magnitude = Math.sqrt(gx * gx + gz * gz);
    if (magnitude < 0.0001) return { pitch: 0, angle: info.angle };
    // Local +Z is deliberately pointed downhill because THREE rotation.x > 0
    // lowers the +Z edge.
    var downX = -gx / magnitude, downZ = -gz / magnitude;
    return { pitch: Math.min(info.pitchRad, Math.atan(magnitude)), angle: Math.atan2(downX, downZ) };
  }

  function addWalls(height){
    var shape=shapeFromRing(localRing);localObstructions.forEach(function(r){var h=new THREE.Path();h.moveTo(r[0][0],r[0][1]);for(var i=1;i<r.length;i++)h.lineTo(r[i][0],r[i][1]);h.closePath();shape.holes.push(h);});
    var g=new THREE.ExtrudeGeometry(shape,{depth:height,bevelEnabled:false,curveSegments:1,steps:1});g.rotateX(-Math.PI/2);
    var mesh=new THREE.Mesh(g,new THREE.MeshStandardMaterial({color:0xd9d4ce,roughness:.88,metalness:0}));mesh.castShadow=true;mesh.receiveShadow=true;(contextGroup||rootGroup).add(mesh);
    // A thin fascia separates the building wall from the roof visually.
    var edge=localRing.map(function(p){return new THREE.Vector3(p[0],height+.01,p[1]);});edge.push(edge[0]);(contextGroup||rootGroup).add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(edge),new THREE.LineBasicMaterial({color:0x73777a})));
  }

  function addRoof(info) {
    var rotated = transformRing(localRing, -info.angle);
    var outer = rotated.map(function (p) { return new THREE.Vector2(p[0], p[1]); });
    var holes = localObstructions.map(function (r) {
      return transformRing(r, -info.angle).map(function (p) { return new THREE.Vector2(p[0], p[1]); });
    });
    var contours = [outer].concat(holes);
    var all = []; contours.forEach(function (c) { all = all.concat(c); });
    var tris = THREE.ShapeUtils.triangulateShape(outer, holes);
    var pos = [];
    tris.forEach(function (t) {
      for (var k = 0; k < 3; k++) {
        var p = all[t[k]];
        var w = rotatePoint([p.x, p.y], info.angle);
        pos.push(w[0], roofHeight(w[0], w[1], info), w[1]);
      }
    });
    if (!pos.length) return;
    var g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.computeVertexNormals();
    var m = new THREE.MeshStandardMaterial({ color: 0x8b8f91, roughness: 0.76, metalness: 0.02, side: THREE.DoubleSide });
    var mesh = new THREE.Mesh(g, m);
    mesh.castShadow = true; mesh.receiveShadow = true;
    mesh.userData={kind:'roof-surface',surfaceId:'authoritative-fallback',baseColor:m.color.getHex()};
    (roofPlanesGroup||rootGroup).add(mesh);

    // Add a subtle roof edge so the plane does not visually disappear into the sky.
    var edgePts = localRing.map(function (p) { return new THREE.Vector3(p[0], roofHeight(p[0], p[1], info) + 0.015, p[1]); });
    edgePts.push(edgePts[0].clone());
    var boundaryLine=new THREE.Line(new THREE.BufferGeometry().setFromPoints(edgePts),new THREE.LineBasicMaterial({color:0xf2a33a,transparent:true,opacity:.98,depthTest:false}));boundaryLine.renderOrder=20;boundaryLine.userData={kind:'authoritative-boundary'};(boundaryGroup&&boundaryGroup.parent===rootGroup?boundaryGroup:rootGroup).add(boundaryLine);
  }

  function addPlaneMeshes(){
    aiPlanes.forEach(function(p,idx){if(!p.polygon||p.polygon.length<3)return;var poly=p.polygon;var pitch=(Number(p.pitch)||0)*Math.PI/180;var az=(Number.isFinite(p.azimuth)?p.azimuth:0)*Math.PI/180;
      var down=[Math.sin(az),-Math.cos(az)], c=polygonCentroid(poly), base=roofInfo.baseY+.045;
      var pts=poly.map(function(q){var d=(q[0]-c[0])*down[0]+(q[1]-c[1])*down[1];return new THREE.Vector3(q[0],base+Math.tan(pitch)*d,q[1]);});
      var pos=[];for(var i=1;i<pts.length-1;i++){pos.push(pts[0].x,pts[0].y,pts[0].z,pts[i].x,pts[i].y,pts[i].z,pts[i+1].x,pts[i+1].y,pts[i+1].z);}
      if(pos.length){var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));geom.computeVertexNormals();var mat=new THREE.MeshStandardMaterial({color:idx%2?0x7e8f99:0x71848f,roughness:.76,metalness:.02,transparent:true,opacity:.24,side:THREE.DoubleSide});var mesh=new THREE.Mesh(geom,mat);mesh.receiveShadow=true;(roofPlanesGroup||rootGroup).add(mesh);}
      var outline=pts.slice();outline.push(pts[0]);(roofPlanesGroup||rootGroup).add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(outline),new THREE.LineBasicMaterial({color:0x4b87a8,transparent:true,opacity:.65})));
    });
  }

  function addStructureBlocks(structures,style){
    structures.forEach(function(item,idx){var r=item.polygon||item;if(!r||r.length<3)return;var c=polygonCentroid(r),measuredHeight=Number(item.height),heightKnown=Number.isFinite(measuredHeight)&&measuredHeight>0,h=heightKnown?measuredHeight:.012;var y=roofHeight(c[0],c[1],roofInfo);var g=new THREE.ExtrudeGeometry(shapeFromRing(r),{depth:h,bevelEnabled:heightKnown,bevelSize:heightKnown?.045:0,bevelThickness:heightKnown?.045:0,bevelSegments:heightKnown?1:0});g.rotateX(-Math.PI/2);g.translate(0,y+.025,0);
      var color=style==='ai'?0xe2a33a:(heightKnown?(idx%2?0x9fa7ad:0xaeb5ba):0xc58b3a),m=new THREE.MeshStandardMaterial({color:color,roughness:.8,transparent:style==='ai'||!heightKnown,opacity:heightKnown?(style==='ai'?.72:1):.42});var mesh=new THREE.Mesh(g,m);mesh.castShadow=heightKnown;mesh.receiveShadow=heightKnown;(structuresGroup||rootGroup).add(mesh);
      if(style==='ai'&&item.type){var label=makeLabel(String(item.type).slice(0,26),0xe2a33a);label.position.set(c[0],y+h+.18,c[1]);(structuresGroup||rootGroup).add(label);}
    });
  }
  function makeLabel(text,color){var canvas=document.createElement('canvas');canvas.width=512;canvas.height=96;var ctx=canvas.getContext('2d');ctx.fillStyle='rgba(255,255,255,.92)';ctx.roundRect?ctx.roundRect(8,10,496,76,16):ctx.fillRect(8,10,496,76);ctx.fill();ctx.fillStyle='#3c4349';ctx.font='700 28px Arial';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(text,256,48);var tex=new THREE.CanvasTexture(canvas);var mat=new THREE.SpriteMaterial({map:tex,transparent:true,depthTest:false});var s=new THREE.Sprite(mat);s.scale.set(3.2,.6,1);return s;}

  function packPanels(requested, info, roof, obstructions) {
    var best = [];
    var baseAngle = azimuthToWorldAngle(currentOrientationDeg);
    var orientations = [{ w: PANEL_W, d: PANEL_D }, { w: PANEL_D, d: PANEL_W }];
    orientations.forEach(function (o) {
      var rr = ringBounds(transformRing(roof, -baseAngle));
      var sx = o.w + PANEL_GAP, sz = o.d + PANEL_GAP, found = [];
      for (var z = rr.minZ + EDGE_SETBACK + o.d / 2; z <= rr.maxZ - EDGE_SETBACK - o.d / 2 + .001; z += sz) {
        for (var x = rr.minX + EDGE_SETBACK + o.w / 2; x <= rr.maxX - EDGE_SETBACK - o.w / 2 + .001; x += sx) {
          var world = rotatePoint([x, z], baseAngle);
          var corners = rect(world[0], world[1], o.w, o.d, baseAngle);
          if (polygonFits(corners, roof, obstructions)) found.push({ x: world[0], z: world[1], w: o.w, d: o.d, angle: baseAngle, plane: null });
        }
      }
      if (found.length > best.length) best = found;
    });
    // AI roof planes: use them only when they are valid and actually overlap the user roof.
    if (aiPlanes.length) {
      var planeBest = [];
      aiPlanes.forEach(function (p) {
        var poly = p.polygon; if (!poly || poly.length < 3) return;
        var c = polygonCentroid(poly);
        var rowAngle = Number.isFinite(p.azimuth) ? (p.azimuth * Math.PI / 180) : baseAngle;
        var rr = ringBounds(transformRing(poly, -rowAngle));
        [{ w: PANEL_W, d: PANEL_D }, { w: PANEL_D, d: PANEL_W }].forEach(function (o) {
          var arr = [], sx = o.w + PANEL_GAP, sz = o.d + PANEL_GAP;
          for (var z = rr.minZ + EDGE_SETBACK + o.d / 2; z <= rr.maxZ - EDGE_SETBACK - o.d / 2 + .001; z += sz) {
            for (var x = rr.minX + EDGE_SETBACK + o.w / 2; x <= rr.maxX - EDGE_SETBACK - o.w / 2 + .001; x += sx) {
              var world = rotatePoint([x, z], rowAngle);
              var corners = rect(world[0], world[1], o.w, o.d, rowAngle);
              if (polygonFits(corners, poly, obstructions)) arr.push({ x: world[0], z: world[1], w: o.w, d: o.d, angle: rowAngle, plane: p });
            }
          }
          if (arr.length > planeBest.length) planeBest = planeBest.concat(arr);
        });
      });
      if (planeBest.length >= Math.min(best.length, 4)) best = planeBest;
    }
    return best.slice(0, Math.max(0, Math.min(160, Number(requested) || 0)));
  }

  function azimuthToWorldAngle(azimuthDeg) {
    // Local world axes: +X = east, +Z = south. Three.js yaw=0 points the
    // module's long axis toward +Z (south), so geographic azimuth maps cleanly
    // to a yaw relative to south.
    return ((Number(azimuthDeg) || 0) - 180) * Math.PI / 180;
  }

  function addShadeOverlay(){
    if(!shadeGrid||!shadeGrid.x||!shadeGrid.z||!shadeGrid.relativeFlux)return;
    var g=shadeGrid, threshold=Number(g.threshold)||0.65;
    var mat=new THREE.MeshBasicMaterial({color:0x596168,transparent:true,opacity:.24,depthWrite:false,side:THREE.DoubleSide});
    var group=new THREE.Group();
    for(var r=0;r<g.rows-1;r++)for(var c=0;c<g.cols-1;c++){
      var vals=[g.relativeFlux[r*g.cols+c],g.relativeFlux[r*g.cols+c+1],g.relativeFlux[(r+1)*g.cols+c],g.relativeFlux[(r+1)*g.cols+c+1]].map(Number);
      if(vals.some(function(v){return !Number.isFinite(v);})||Math.min.apply(null,vals)>=threshold)continue;
      var x0=g.x[c],x1=g.x[c+1],z0=g.z[r],z1=g.z[r+1];
      var pts=[[x0,z0],[x1,z0],[x1,z1],[x0,z1]];
      var pos=[];pts.forEach(function(q){var y=siteHeightAt(q[0],q[1]);if(y===null)y=roofInfo?roofHeight(q[0],q[1],roofInfo):0;pos.push(q[0],y+.035,q[1]);});
      var geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));geom.setIndex([0,1,2,0,2,3]);geom.computeVertexNormals();group.add(new THREE.Mesh(geom,mat));
    }
    (shadeOverlayGroup||rootGroup).add(group);
  }

  function addClassificationOverlay(){
    var g=shadeGrid;if(!shadeOverlayGroup||!g||!g.classifications)return;
    var step=Number(g.resolution_meters),ox=Number(g.origin_x),oz=Number(g.origin_z);
    if(!Number.isFinite(step)||step<=0)return;
    var palette={usable:[0x4d9d75,.10],shaded:[0x6d587a,.34],unknown:[0x8c9591,.18],setback:[0xd99b42,.26],obstruction:[0xb95145,.34]},materials={};
    Object.keys(palette).forEach(function(kind){materials[kind]=new THREE.MeshBasicMaterial({color:palette[kind][0],transparent:true,opacity:palette[kind][1],depthWrite:false,side:THREE.DoubleSide});});
    for(var r=0;r<g.rows;r++)for(var c=0;c<g.cols;c++){
      var kind=g.classifications[r*g.cols+c];if(!materials[kind])continue;
      var x0=ox+c*step,x1=x0+step,z0=oz+r*step,z1=z0+step,pts=[[x0,z0],[x1,z0],[x1,z1],[x0,z1]];
      if(!pts.every(function(point){return pointInRing(point[0],point[1],localRing)&&surfaceAt(point[0],point[1]);}))continue;
      var positions=[];pts.forEach(function(point){positions.push(point[0],roofHeightFromSurfaces(point[0],point[1])+.045,point[1]);});
      var geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setIndex([0,1,2,0,2,3]);geometry.computeVertexNormals();var mesh=new THREE.Mesh(geometry,materials[kind]);mesh.userData={kind:'shade-classification',classification:kind};shadeOverlayGroup.add(mesh);
    }
  }

  function updateShadeStatus(){
    var badge=document.getElementById('roof3d-shade-badge'),modeBadge=document.getElementById('roof3d-layout-mode-badge'),note=document.getElementById('roof3d-shade-note'),areas=shadeGrid&&shadeGrid.areas_sqm||{};
    function area(id,key){var element=document.getElementById(id);if(element)element.textContent=Number(areas[key]||0).toFixed(1)+' m²';}
    area('roof3d-area-usable','usable');area('roof3d-area-shaded','shaded');area('roof3d-area-obstruction','obstruction');area('roof3d-area-setback','setback');area('roof3d-area-unknown','unknown');
    if(modeBadge){modeBadge.textContent=panelLayoutMode==='planning_preview'?'Planning preview — shade unverified':'Conservative layout — confirmed usable cells only';modeBadge.classList.toggle('is-preview',panelLayoutMode==='planning_preview');}
    if(!badge)return;
    if(!shadeGrid||shadeGrid.status==='insufficient_data'||!classificationGridValid(shadeGrid)){
      var incompatible=shadeGrid&&shadeGrid.geometry_compatible===false;badge.textContent=incompatible?'Shade classification · incompatible with current roof':'Shade unverified · obstruction and setback rules active';badge.classList.add('is-error');badge.classList.remove('is-ready');
      if(note)note.textContent=incompatible?(panelLayoutMode==='planning_preview'?'The incompatible shade grid is ignored. Preview panels are not shade-verified.':'The incompatible shade grid is ignored. Conservative mode fails closed and places no panels.'):(panelLayoutMode==='planning_preview'?'Planning-preview panels may occupy unknown cells. They are not shade-verified, and saved generation estimates do not include validated shading losses.':'Unknown regions have not been shade-verified. Conservative mode excludes them from panel placement.');
    }else{
      var source=String(shadeGrid.source&&shadeGrid.source.type||'available').replaceAll('_',' ');badge.textContent='Shade classification · '+source;badge.classList.add('is-ready');badge.classList.remove('is-error');
      if(note)note.textContent='Panel placement excludes classified shade, obstruction footprints, safety setbacks, and any unknown cells required by policy.';
    }
  }

  function addPanels(requested, info) {
    shadeAvoidedPanels=0;
    var panels = packPanels(requested, info, localRing, localObstructions);
    var mat = new THREE.MeshStandardMaterial({ color: 0x124f91, roughness: .24, metalness: .34 });
    var frameMat = new THREE.MeshStandardMaterial({ color: 0x1e252b, roughness: .4, metalness: .62 });
    panels.forEach(function (p) {
      var slope = roofSurfaceSlope(p.x, p.z, info);
      if (p.plane && Number.isFinite(Number(p.plane.pitch))) slope.pitch = Math.min(info.pitchRad || Math.PI/4, Math.abs(Number(p.plane.pitch) * Math.PI/180));
      var pitch = slope.pitch;
      // The compass controls the electrical-facing azimuth of the PV array.
      // The roof pitch is retained so the visual remains a plausible mounted
      // system rather than changing the building geometry itself.
      var angle = azimuthToWorldAngle(currentOrientationDeg);
      var y = roofHeight(p.x, p.z, info) + .10;
      var group = new THREE.Group();
      var panel = new THREE.Mesh(new THREE.BoxGeometry(p.w, PANEL_THICKNESS, p.d), mat);
      var frame = new THREE.Mesh(new THREE.BoxGeometry(p.w + .055, .026, p.d + .055), frameMat);
      frame.position.y = -.012; group.add(frame); group.add(panel);
      group.rotation.order = 'YXZ';
      group.rotation.y = angle;
      group.rotation.x = pitch;
      group.position.set(p.x, y, p.z);
      group.castShadow = true;
      (panelsGroup||rootGroup).add(group);
      if (info.type === 'flat' && !p.plane) {
        var r1 = new THREE.Mesh(new THREE.BoxGeometry(p.w + .08, .035, .035), frameMat);
        var r2 = r1.clone();
        r1.position.set(0, -.08, -p.d * .31); r2.position.set(0, -.08, p.d * .31);
        group.add(r1); group.add(r2);
      }
    });
    updatePanelStatus(panels.length, requested);
    return panels.length;
  }

  function addGround(){var bounds=ringBounds(localRing),padding=Math.max(1.2,Math.min(Math.max(bounds.width,bounds.depth)*.12,3.5)),w=Math.max(bounds.width+padding*2,5),d=Math.max(bounds.depth+padding*2,5),g=new THREE.Mesh(new THREE.PlaneGeometry(w,d),new THREE.MeshStandardMaterial({color:0xe9e5dc,roughness:1,transparent:true,opacity:.58}));g.rotation.x=-Math.PI/2;g.position.set(bounds.cx,-.04,bounds.cz);g.receiveShadow=true;g.userData={kind:'ground-context'};(contextGroup&&contextGroup.parent===rootGroup?contextGroup:rootGroup).add(g);}

  function setGeometryAvailability(available){var fallback=document.getElementById('roof-3d-fallback');if(!fallback)return;if(available){fallback.classList.add('hidden');fallback.style.display='none';return;}fallback.textContent='3D roof geometry unavailable because no valid authoritative roof boundary is saved.';fallback.classList.remove('hidden');fallback.style.display='block';}

  function buildRoofModel(pitchDeg,panelCount){if(!rootGroup)return;currentPitch=Math.max(0,Math.min(40,Number(pitchDeg)||0));currentPanels=panelCount;if(siteRoofModel){buildSiteRoofModel(currentPanels);return;}resetViewerGroups();
    if(!localRing||localRing.length<3){currentPlacedPanels=[];setGeometryAvailability(false);updatePanelStatus([],panelCount);return;}setGeometryAvailability(true);
    var bounds=ringBounds(localRing);footprintSpan=Math.max(Math.min(Math.max(bounds.width,bounds.depth),30),7);var angle=principalAngle(localRing),rotated=transformRing(localRing,-angle),rb=ringBounds(rotated),effective=roofType==='flat'?0:currentPitch;
    var riseRun=(roofType==='shed'?rb.depth:(roofType==='hip'?Math.min(rb.width,rb.depth)/2:rb.depth/2));
    var info={type:roofType,baseY:2.35,angle:angle,pitchRad:effective*Math.PI/180,halfD:Math.max(rb.depth/2,.5),halfW:Math.max(rb.width/2,.5),centerZ:rb.cz,centerX:rb.cx,minZ:rb.minZ,depth:Math.max(rb.depth,.5),rise:Math.tan(effective*Math.PI/180)*riseRun};roofInfo=info;
    addGround();addWalls(info.baseY);addRoof(info);addPlaneMeshes();addStructureBlocks(localObstructions.map(function(r){return{polygon:r};}),'user');addStructureBlocks(aiObstructions,'ai');addPanels(currentPanels,info);applyViewPresentation();updateOrientation(info);fitCamera(userInteracted||viewMode!=='3d');
  }
  function cardinalDirection(angle){var deg=((angle*180/Math.PI)+360)%360;var dirs=['N','NE','E','SE','S','SW','W','NW'];return dirs[Math.round(deg/45)%8];}
  function updateCompassMarker(){
    var ring=document.querySelector('.roof3d-compass-ring'),sun=document.querySelector('.roof3d-sun');
    if(!ring||!sun)return;
    var deg=((Number(currentOrientationDeg)||0)%360+360)%360,rad=deg*Math.PI/180;
    // Keep the handle on the visible outer ellipse.
    sun.style.left=(50+42*Math.sin(rad))+'%';
    sun.style.top=(50-38*Math.cos(rad))+'%';
    sun.style.bottom='auto';
    sun.style.transform='translate(-50%,-50%)';
    sun.title='Drag to change solar orientation';
    ring.setAttribute('aria-valuenow',String(Math.round(deg)));
    ring.setAttribute('aria-valuetext',Math.round(deg)+' degrees '+cardinalDirection(rad));
  }

  function updateOrientation(info){
    var t=document.getElementById('roof3d-orientation'),d=document.getElementById('roof3d-orientation-detail');
    if(!t||!d)return;
    var heading=((Number(currentOrientationDeg)||0)%360+360)%360;
    var selected=reconstructedSurfaces.find(function(surface){return (surface.parentId||surface.id)===selectedSurfaceId;})||reconstructedSurfaces[0];
    var pitch=Math.round(Number(selected?selected.pitch:(aiAnalysis?.pitch_degrees ?? currentPitch))||0);
    t.textContent='Solar orientation · '+cardinalDirection(heading*Math.PI/180)+' · '+Math.round(heading)+'°';
    d.textContent=selected&&selected.pitch===0?'Panels follow the flat roof plane · mounting tilt not modelled · drag the compass to orient':(!selected&&roofType==='flat'?'Simulated mounting tilt '+Math.round(FLAT_PANEL_TILT*180/Math.PI)+'° · drag the compass to optimize':'Panels facing '+Math.round(heading)+'° · '+pitch+'° roof pitch · drag the compass');
    var detail=document.getElementById('roof3d-ai-details');
    if(detail){
      var aiText=aiAnalysis?' AI vision: '+(aiAnalysis.roof_type||roofType)+' roof, '+pitch+'° pitch, advisory confidence '+Math.round((Number(aiAnalysis.confidence)||0)*100)+'%.':'';
      detail.textContent=aiText+' Orientation preview: '+Math.round(heading)+'° ('+cardinalDirection(heading*Math.PI/180)+').';
    }
    updateCompassMarker();
  }

  function updateOrientationStatus(result){
    if(result&&result.chart_data)window.dispatchEvent(new CustomEvent('signix:chart-data',{detail:result.chart_data}));
    if(!result)return;
    var gen=Number(result.annual_generation);
    var savings=Number(result.monthly_savings);
    var factor=Number(result.orientation_factor);
    var label=result.orientation_label||cardinalDirection(currentOrientationDeg*Math.PI/180);
    if(Number.isFinite(gen)){
      document.querySelectorAll('[data-orientation-annual-generation]').forEach(function(el){el.textContent=Math.round(gen).toLocaleString('en-IN');});
    }
    if(Number.isFinite(savings)){
      document.querySelectorAll('[data-orientation-monthly-savings]').forEach(function(el){el.textContent='₹'+Math.round(savings).toLocaleString('en-IN');});
    }
    var factorEl=document.getElementById('roof3d-orientation-factor');
    if(factorEl&&Number.isFinite(factor))factorEl.textContent=Math.round(factor*100)+'% relative yield';
    var deltaEl=document.getElementById('roof3d-orientation-delta');
    if(deltaEl&&Number.isFinite(gen)&&typeof ANNUAL_GENERATION!=='undefined'){
      var delta=gen-savedAnnualGeneration;
      deltaEl.textContent=(delta>=0?'+':'')+Math.round(delta).toLocaleString('en-IN')+' kWh/yr vs saved orientation';
    }
    var apply=document.getElementById('roof3d-orientation-apply');
    syncOrientationApplyButton(apply,orientationDirty);
    var status=document.getElementById('roof-ai-status');
    if(status)status.textContent=(orientationDirty?'Orientation preview · ':'Saved orientation · ')+label+' · '+Math.round((factor||0)*100)+'% relative yield.'+(orientationDirty?' Click Apply orientation to save.':'');
  }

  function angularDifference(a,b){var difference=Math.abs((((Number(a)||0)-(Number(b)||0)+540)%360)-180);return difference;}
  function invalidateOrientationPreview(){clearTimeout(orientationPreviewTimer);orientationPreviewTimer=null;return++orientationRequestSeq;}
  function requestOrientationPreview(expectedSeq){
    var locationId=typeof SIGNIX_LOCATION_ID!=='undefined'?SIGNIX_LOCATION_ID:null;
    if(!locationId)return;
    var seq=expectedSeq===undefined?invalidateOrientationPreview():expectedSeq;if(seq!==orientationRequestSeq)return;
    var status=document.getElementById('roof-ai-status');if(status)status.textContent='Calculating generation for '+Math.round(currentOrientationDeg)+'°…';
    var csrf=document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')||'';
    fetch('/api/estimate/'+encodeURIComponent(locationId)+'/orientation-preview',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf,'X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({orientation:currentOrientationDeg})})
      .then(function(res){return res.json().then(function(data){return{ok:res.ok,data:data};});})
      .then(function(out){if(seq!==orientationRequestSeq)return;if(!out.ok)throw new Error(out.data.error||'Preview failed');updateOrientationStatus(out.data);})
      .catch(function(err){if(seq!==orientationRequestSeq)return;var status=document.getElementById('roof-ai-status');if(status)status.textContent='Orientation changed visually. Generation preview unavailable right now.';console.warn('Orientation preview failed',err);});
  }

  function setOrientation(deg, preview){
    var n=Number(deg);if(!Number.isFinite(n))return;
    var seq=invalidateOrientationPreview();currentOrientationDeg=((n%360)+360)%360;orientationDirty=angularDifference(currentOrientationDeg,savedOrientationDeg)>0.1;
    var azimuth=document.getElementById('roof3d-panel-azimuth');if(azimuth)azimuth.value=String(Math.round(currentOrientationDeg));
    buildRoofModel(currentPitch,currentPanels);
    syncOrientationApplyButton(document.getElementById('roof3d-orientation-apply'),orientationDirty);
    if(preview)orientationPreviewTimer=setTimeout(function(){requestOrientationPreview(seq);},180);
  }

  function bindOrientationCompass(){
    var ring=document.querySelector('.roof3d-compass-ring');if(!ring)return;
    var dragging=false;
    function readDeg(e){var r=ring.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2,dx=e.clientX-cx,dy=e.clientY-cy;var deg=Math.atan2(dx,-dy)*180/Math.PI;return(deg+360)%360;}
    function move(e){if(!dragging)return;setOrientation(readDeg(e),true);}
    ring.addEventListener('pointerdown',function(e){dragging=true;userInteracted=true;ring.setPointerCapture?.(e.pointerId);setOrientation(readDeg(e),true);e.preventDefault();});
    ring.addEventListener('pointermove',move);
    ['pointerup','pointercancel','lostpointercapture'].forEach(function(ev){ring.addEventListener(ev,function(){dragging=false;});});
    ring.setAttribute('role','slider');ring.setAttribute('tabindex','0');ring.setAttribute('aria-label','Solar panel orientation');ring.setAttribute('aria-valuemin','0');ring.setAttribute('aria-valuemax','359');
    ring.addEventListener('keydown',function(e){var step=e.shiftKey?15:5;if(e.key==='ArrowLeft'||e.key==='ArrowDown'){setOrientation(currentOrientationDeg-step,true);e.preventDefault();}else if(e.key==='ArrowRight'||e.key==='ArrowUp'){setOrientation(currentOrientationDeg+step,true);e.preventDefault();}else if(e.key==='Home'){setOrientation(180,true);e.preventDefault();}});
    updateCompassMarker();
  }

  async function applyOrientation(){
    if(!orientationDirty)return;
    var locationId=typeof SIGNIX_LOCATION_ID!=='undefined'?SIGNIX_LOCATION_ID:null;if(!locationId)return;
    invalidateOrientationPreview();var requestedOrientation=currentOrientationDeg;orientationSaving=true;
    var apply=document.getElementById('roof3d-orientation-apply');if(apply){apply.disabled=true;apply.textContent='Saving…';}
    var csrf=document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')||'';
    try{
      var res=await fetch('/api/estimate/'+encodeURIComponent(locationId)+'/orientation',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf,'X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({orientation:requestedOrientation})});
      var data=await res.json();if(!res.ok)throw new Error(data.error||'Could not save orientation');
      savedOrientationDeg=requestedOrientation;savedAnnualGeneration=Number(data.annual_generation||savedAnnualGeneration);orientationDirty=angularDifference(currentOrientationDeg,savedOrientationDeg)>0.1;orientationSaving=false;if(!orientationDirty)updateOrientationStatus(data);else{clearTimeout(orientationPreviewTimer);orientationPreviewTimer=null;requestOrientationPreview(orientationRequestSeq);}
      if(!orientationDirty)document.querySelectorAll('[data-orientation-annual-generation]').forEach(function(el){el.dataset.original=el.textContent;});
      if(apply){apply.textContent=orientationDirty?'Apply orientation':'✓ Orientation applied';syncOrientationApplyButton(apply,orientationDirty);if(!orientationDirty)setTimeout(function(){apply.textContent='Apply orientation';syncOrientationApplyButton(apply,orientationDirty);},1400);}
      var banner=document.getElementById('orientation-save-status');if(banner)banner.textContent='Saved to this estimate.';
    }catch(err){orientationSaving=false;if(apply){apply.textContent='Apply orientation';syncOrientationApplyButton(apply,orientationDirty);}var status=document.getElementById('roof-ai-status');if(status)status.textContent='Could not save orientation: '+err.message;}
  }

  function undoOrientation(){setOrientation(savedOrientationDeg,false);orientationDirty=false;var apply=document.getElementById('roof3d-orientation-apply');if(apply){apply.disabled=true;apply.textContent='Apply orientation';}var seq=orientationRequestSeq;requestOrientationPreview(seq);}
  function updatePanelStatus(panelsOrCount,requested){var panels=Array.isArray(panelsOrCount)?panelsOrCount:null,placed=panels?panels.length:Number(panelsOrCount)||0,e=document.getElementById('roof-3d-panel-status'),moduleWatts=typeof MODULE_WATTAGE_W!=='undefined'?Number(MODULE_WATTAGE_W):NaN,metrics=panelLayoutMetrics(panels||[],moduleWatts),preview=metrics.preview,confirmed=metrics.confirmed,selectedKw=typeof SYSTEM_SIZE!=='undefined'?Number(SYSTEM_SIZE):NaN,layoutKw=panels?metrics.totalKw:layoutCapacityKw(placed,moduleWatts),shadeUnverified=shadeGrid&&shadeGrid.status==='insufficient_data',message=panelLayoutMode==='planning_preview'&&preview>0?placed+' physically feasible preview modules placed; '+preview+' are not shade-verified.':shadeUnverified&&placed===0?'No panels placed: conservative mode requires confirmed usable cells.':placed+' shade-verified physical modules placed on valid reconstructed surfaces.',shortfall=placementShortfallText(placed,requested,placementRejections);if(shortfall)message+=' '+shortfall;if(layoutDiffersFromEstimate(layoutKw,selectedKw))message+=' Visible layout capacity '+layoutKw.toFixed(2)+' kWp differs from the selected '+selectedKw.toFixed(2)+' kW estimate.';if(e)e.textContent=message;var c=document.getElementById('roof3d-module-count');if(c)c.innerHTML=String(placed)+' <small>modules</small>';var capacity=document.getElementById('roof3d-layout-capacity');if(capacity)capacity.innerHTML=layoutKw===null?'Unavailable':layoutKw.toFixed(2)+' <small>kWp</small>';var previewCount=document.getElementById('roof3d-preview-module-count');if(previewCount)previewCount.textContent=String(preview);var previewCapacity=document.getElementById('roof3d-preview-capacity');if(previewCapacity)previewCapacity.textContent=Number.isFinite(metrics.previewKw)?metrics.previewKw.toFixed(2)+' kWp':'Unavailable';var confirmedCount=document.getElementById('roof3d-confirmed-module-count');if(confirmedCount)confirmedCount.textContent=String(confirmed);var confirmedCapacity=document.getElementById('roof3d-confirmed-capacity');if(confirmedCapacity)confirmedCapacity.textContent=Number.isFinite(metrics.confirmedKw)?metrics.confirmedKw.toFixed(2)+' kWp':'Unavailable';}
  function updateAcceptedLayoutStatus(accepted){var metrics=acceptedLayoutMetrics(accepted),count=document.getElementById('roof3d-accepted-module-count'),capacity=document.getElementById('roof3d-accepted-capacity'),status=document.getElementById('roof3d-layout-acceptance-status');if(count)count.textContent=metrics.status==='accepted'?String(metrics.count):metrics.status==='stale'?'Stale':metrics.status==='invalid'?'Unavailable':'None';if(capacity)capacity.textContent=metrics.status==='accepted'?metrics.capacityKw.toFixed(2)+' kWp':'—';if(status&&metrics.status==='accepted')status.textContent='Accepted layout · '+metrics.count+' modules · '+metrics.capacityKw.toFixed(2)+' kWp. '+(accepted.shade_verified?'Shade-classified':'Shade remains unverified')+'.';else if(status&&metrics.status==='stale')status.textContent='Previously accepted layout is stale. Reconstruct and accept a current proposal.';else if(status&&metrics.status==='invalid')status.textContent='Accepted layout metrics are unavailable. Reconstruct and validate a current proposal.';else if(status)status.textContent='Preview only — not accepted.';}
  function parseGeometryFromGlobals(){localRing=null;localObstructions=[];var raw=extractRing(typeof ROOF_POLYGON_GEOJSON!=='undefined'?ROOF_POLYGON_GEOJSON:null),origin=geographicOrigin(raw);projectionOrigin=origin;var r=geoJsonRingToLocal(raw,origin);if(r)localRing=r;var obs=typeof ROOF_OBSTRUCTIONS_GEOJSON!=='undefined'?ROOF_OBSTRUCTIONS_GEOJSON:null;if(obs&&obs.coordinates){var list=obs.type==='MultiPolygon'?obs.coordinates:[obs.coordinates];list.forEach(function(item){var loc=geoJsonRingToLocal(item[0]||item,origin);if(loc&&loc.length>=3)localObstructions.push(loc);});}}
  function reconstructionBounds(){
    var ring=localRing&&localRing.length?localRing:[[-4,-3],[4,-3],[4,3],[-4,3]],bounds=ringBounds(ring),minY=0,maxY=roofInfo?roofInfo.baseY+Math.max(roofInfo.rise,0):3;
    reconstructedSurfaces.forEach(function(surface){surface.polygon.forEach(function(point){var y=surfaceHeight(surface,point[0],point[1]);minY=Math.min(minY,y);maxY=Math.max(maxY,y);});});
    reconstructedStructures.forEach(function(item){if(!item.polygon)return;var center=polygonCentroid(item.polygon),measuredHeight=Number(item.height),height=Number.isFinite(measuredHeight)&&measuredHeight>0?measuredHeight:0,top=roofHeightFromSurfaces(center[0],center[1])+height;maxY=Math.max(maxY,top);});
    return{minX:bounds.minX,maxX:bounds.maxX,minZ:bounds.minZ,maxZ:bounds.maxZ,minY:minY,maxY:maxY,width:Math.max(bounds.width,.5),depth:Math.max(bounds.depth,.5),height:Math.max(maxY-minY,.5),cx:bounds.cx,cz:bounds.cz,cy:(minY+maxY)/2};
  }
  function fitCamera(preserveView){if(!camera)return;var bounds=reconstructionBounds(),container=document.getElementById('roof-3d-container'),aspect=Math.max((container?.clientWidth||600)/(container?.clientHeight||420),.35);if(!preserveView){cameraAzimuth=.72;cameraElevation=.55;}cameraFitDistance=cameraFrameForBounds(bounds,aspect,cameraAzimuth,cameraElevation);cameraDistance=cameraFitDistance;cameraTargetX=bounds.cx;cameraTargetY=bounds.cy+.18;cameraTargetZ=bounds.cz;camera.near=.1;camera.far=Math.max(250,cameraDistance*12);updateCamera();}
  function updateCamera(){if(!camera)return;var container=document.getElementById('roof-3d-container'),aspect=Math.max((container?.clientWidth||600)/(container?.clientHeight||420),.2),view=Math.max(cameraDistance,5.5);if(camera.isOrthographicCamera){camera.left=-view*aspect/2;camera.right=view*aspect/2;camera.top=view/2;camera.bottom=-view/2;camera.updateProjectionMatrix();}var targetY=cameraTargetY,positionDistance=Math.max(view*1.8,12),ce=Math.cos(cameraElevation),se=Math.sin(cameraElevation);camera.position.set(cameraTargetX+Math.cos(cameraAzimuth)*ce*positionDistance,targetY+se*positionDistance,cameraTargetZ+Math.sin(cameraAzimuth)*ce*positionDistance);camera.lookAt(cameraTargetX,targetY,cameraTargetZ);}
  function bindCameraControls(container){var dragging=false,lastX=0,lastY=0;container.addEventListener('pointerdown',function(e){dragging=true;userInteracted=true;lastX=e.clientX;lastY=e.clientY;container.setPointerCapture?.(e.pointerId);});container.addEventListener('pointermove',function(e){if(!dragging)return;var dx=e.clientX-lastX,dy=e.clientY-lastY;lastX=e.clientX;lastY=e.clientY;if(e.shiftKey||e.buttons===4){var scale=cameraDistance/500;cameraTargetX-=dx*scale;cameraTargetZ-=dy*scale;}else{cameraAzimuth-=dx*.008;cameraElevation=Math.max(.16,Math.min(1.56,cameraElevation+dy*.006));}updateCamera();});['pointerup','pointercancel','lostpointercapture'].forEach(function(ev){container.addEventListener(ev,function(){dragging=false;});});container.addEventListener('wheel',function(e){e.preventDefault();userInteracted=true;var minimum=Math.max(cameraFitDistance*.42,3.5),maximum=Math.max(cameraFitDistance*4,32);cameraDistance=Math.max(minimum,Math.min(maximum,cameraDistance*(1+e.deltaY*.0008)));updateCamera();},{passive:false});}
  function setRoofType(type){roofType=['flat','gable','hip','shed','mixed'].indexOf(type)>=0?type:'flat';buildRoofModel(currentPitch,currentPanels);}
  function stageAIAnalysis(a){if(!a||typeof a!=='object')return;aiAnalysis=a;aiStructuresApplied=false;var bbox=a.imagery_bbox;aiPlanes=[];aiObstructions=[];
    function normToLocal(poly){return normalizedImagePolygonToLocal(poly,bbox,projectionOrigin);}
    (a.roof_planes||[]).forEach(function(p){var loc=normToLocal(p.polygon||[]);if(loc&&loc.length>=3)aiPlanes.push({polygon:loc,pitch:Number(p.pitch_degrees)||0,azimuth:Number.isFinite(Number(p.azimuth_degrees))?Number(p.azimuth_degrees):null,confidence:Number(p.confidence)||0});});
    (a.obstructions||[]).forEach(function(o){var loc=normToLocal(o.polygon||[]);if(loc&&loc.length>=3)aiObstructions.push({polygon:loc,type:o.type||'detected structure',confidence:Number(o.confidence)||0,height:null,renderHeight:.35});});
  }
  function applyAIAnalysis(a){stageAIAnalysis(a);if(!a||typeof a!=='object')return;
    if(a.roof_type&&a.roof_type!=='unknown'){roofType=String(a.roof_type).toLowerCase();var ss=document.getElementById('roof-shape-select');if(ss)ss.value=roofType;}
    var pitch=Number(a.pitch_degrees);if(Number.isFinite(pitch)){currentPitch=Math.max(0,Math.min(40,pitch));var ps=document.getElementById('roof-pitch-select');if(ps){var vals=[0,5,10,15,20,25,30,35,40],near=vals.reduce(function(x,y){return Math.abs(y-pitch)<Math.abs(x-pitch)?y:x;},0);ps.value=String(near);}}
    buildRoofModel(currentPitch,currentPanels);
  }
  function applyAIStructures(){
    if(!aiObstructions.length)return;
    aiObstructions.forEach(function(item){
      if(!item.polygon||item.polygon.length<3)return;
      var center=polygonCentroid(item.polygon), duplicate=localObstructions.some(function(o){var c=polygonCentroid(o);return Math.hypot(c[0]-center[0],c[1]-center[1])<0.65;});
      if(!duplicate)localObstructions.push(item.polygon);
    });
    aiStructuresApplied=true;
    var b=document.getElementById('roof-ai-apply-btn');if(b)b.textContent='✓ Structures applied';
    var s=document.getElementById('roof-ai-status');if(s)s.textContent='AI-detected rooftop structures are now excluded from panel placement.';
    buildRoofModel(currentPitch,currentPanels);
  }

  async function loadSatelliteReference(){
    if(!localRing||!window.ROOF_POLYGON_GEOJSON)return;
    var badge=document.getElementById('roof3d-satellite-badge');
    try{var res=await fetch('/api/roof-ai/imagery',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'image/jpeg','X-Requested-With':'XMLHttpRequest','X-CSRFToken':document.querySelector('meta[name=\"csrf-token\"]')?.getAttribute('content')||''},body:JSON.stringify({polygon:ROOF_POLYGON_GEOJSON.coordinates?.[0]||[]})});if(!res.ok)throw new Error('imagery unavailable');var blob=await res.blob(),url=URL.createObjectURL(blob);var loader=new THREE.TextureLoader();loader.load(url,function(tex){satelliteTexture=tex;var overlay=new THREE.Mesh(new THREE.PlaneGeometry(footprintSpan*1.28,footprintSpan*1.28),new THREE.MeshBasicMaterial({map:tex,transparent:true,opacity:.20,depthWrite:false}));overlay.rotation.x=-Math.PI/2;overlay.position.y=-.02;overlay.scale.set(1,.72,1);rootGroup.add(overlay);if(badge){badge.textContent='Satellite reference · ready';badge.classList.add('is-ready');}});}catch(e){if(badge){badge.textContent='Satellite reference · unavailable';badge.classList.add('is-error');}}
  }
  async function loadSiteRoofModel(){
    var locationId=typeof SIGNIX_LOCATION_ID!=='undefined'?SIGNIX_LOCATION_ID:null; if(!locationId)return;
    var requestSeq=++siteModelRequestSeq,badge=document.getElementById('roof3d-satellite-badge'),status=document.getElementById('roof-ai-status'),accept=document.getElementById('roof3d-layout-accept');
    siteModelLoading=true;
    if(accept)accept.disabled=true;
    if(badge){badge.textContent='Rooftop data · loading…';badge.classList.remove('is-error');}
    if(status)status.textContent='Loading available location-specific rooftop data…';
    try{
      var res=await fetch('/api/roof-model/'+encodeURIComponent(locationId)+'?high_fidelity=0',{credentials:'same-origin',headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}});
      var data=await res.json(),payloadState=reconstructionPayloadState(data);if(requestSeq!==siteModelRequestSeq)return;if(!res.ok||!payloadState.ok)throw new Error(data.error||(payloadState.code==='malformed'?'Rooftop reconstruction response was malformed.':'Location-specific roof data unavailable'));
      siteRoofModel=data; siteModelLoading=false;
      shadeGrid=data.shade_analysis||null; shadeSide=null;
      if(shadeGrid)shadeGrid.geometry_compatible=Boolean(data.geometry_key&&shadeGrid.geometry_key===data.geometry_key);
      panelLayoutMode=shadeGrid&&shadeGrid.placement_policy&&shadeGrid.placement_policy.default_mode==='conservative'?'conservative':'planning_preview';
      var layoutControl=document.getElementById('roof3d-layout-mode');if(layoutControl)layoutControl.value=panelLayoutMode;
      var accepted=data.layout_acceptance;updateAcceptedLayoutStatus(accepted);
      var segs=data.surfaces||[];
      if(badge){badge.textContent=data.mode==='provider'?'Rooftop reconstruction · provider':(data.mode==='prototype_fixture'?'Rooftop reconstruction · prototype fixture':'Rooftop reconstruction · user boundary');badge.classList.add('is-ready');badge.classList.remove('is-error');}
      var shadeBadge=document.getElementById('roof3d-shade-badge');
      if(shadeBadge)updateShadeStatus();
      if(status){
        status.textContent=(data.status_badge?.detail||'Rooftop reconstruction loaded')+' · '+segs.length+' surface fragment'+(segs.length===1?'':'s')+'. Your drawn boundary remains authoritative.';
      }
      buildRoofModel(currentPitch,currentPanels);
    }catch(err){if(requestSeq!==siteModelRequestSeq)return;siteModelLoading=false;siteRoofModel=null;reconstructedSurfaces=[];reconstructedStructures=[];shadeGrid=null;shadeSide=null;currentPlacedPanels=[];buildRoofModel(currentPitch,currentPanels);resolveReconstructionFailure('Location-specific data unavailable: '+err.message);if(badge){badge.textContent='Geographic rooftop data · unavailable';badge.classList.add('is-error');}console.warn('Site roof data unavailable',err);}
  }

  function init(){var container=document.getElementById('roof-3d-container'),fallback=document.getElementById('roof-3d-fallback');if(!container)return;var capability=viewerCapability(typeof THREE!=='undefined',typeof THREE!=='undefined'&&hasWebGL());if(!capability.ok){resolveViewerUnavailable(capability.code);return;}if(fallback){fallback.classList.add('hidden');fallback.style.display='none';}
    try{parseGeometryFromGlobals();var width=container.clientWidth||600,height=container.clientHeight||420,aspect=width/height,view=18;scene=new THREE.Scene();camera=new THREE.OrthographicCamera(-view*aspect/2,view*aspect/2,view/2,-view/2,.1,1000);renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));renderer.setSize(width,height);renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;container.appendChild(renderer.domElement);}catch(error){console.error('SIGNIX 3D renderer initialization failed',error);resolveViewerUnavailable('renderer_initialization_failed',error&&error.message?error.message:'');return;}
    scene.add(new THREE.HemisphereLight(0xeaf5ff,0x6c6259,1.3));var sun=new THREE.DirectionalLight(0xffe7bf,2.35);sun.position.set(12,24,15);sun.castShadow=true;sun.shadow.mapSize.width=2048;sun.shadow.mapSize.height=2048;scene.add(sun);rootGroup=new THREE.Group();scene.add(rootGroup);
    var ps=document.getElementById('roof-pitch-select'),ss=document.getElementById('roof-shape-select');currentPitch=ps?Number(ps.value)||0:0;roofType=ss?ss.value:'flat';currentPanels=Number(typeof PANEL_COUNT!=='undefined'?PANEL_COUNT:0)||0;buildRoofModel(currentPitch,currentPanels);bindCameraControls(container);bindSurfaceSelection(container);bindOrientationCompass();bindPlannerControls();loadSiteRoofModel();
    if(ps)ps.addEventListener('change',function(){currentPitch=Number(this.value)||0;aiPlanes=[];buildRoofModel(currentPitch,currentPanels);});if(ss)ss.addEventListener('change',function(){roofType=this.value;aiPlanes=[];buildRoofModel(currentPitch,currentPanels);});
    function animate(){animationId=requestAnimationFrame(animate);renderer.render(scene,camera);}animate();function resizeViewer(){var w=container.clientWidth,h=container.clientHeight;if(w<10||h<10)return;renderer.setSize(w,h);updateCamera();}window.addEventListener('resize',resizeViewer);if(typeof ResizeObserver!=='undefined')new ResizeObserver(resizeViewer).observe(container);
    window.SIGNIX_ROOF3D={setPitch:function(d){currentPitch=Number(d)||0;buildRoofModel(currentPitch,currentPanels);},setPanelCount:function(n){currentPanels=Number(n)||0;buildRoofModel(currentPitch,currentPanels);},setPitchAndPanels:function(d,n){currentPitch=Number(d)||0;currentPanels=Number(n)||0;buildRoofModel(currentPitch,currentPanels);},setRoofType:setRoofType,setOrientation:setOrientation,applyOrientation:applyOrientation,undoOrientation:undoOrientation,setPanelLayoutMode:setPanelLayoutMode,stageAIAnalysis:stageAIAnalysis,applyAIAnalysis:applyAIAnalysis,applyAIStructures:applyAIStructures,hasRealFootprint:function(){return!!(localRing&&localRing.length>=3);},getPlacedPanelCount:function(){return document.getElementById('roof-3d-panel-status')?.textContent||'';},loadSiteRoofModel:loadSiteRoofModel,setViewMode:setViewMode,setLayerVisible:function(name,visible){var input=document.querySelector('[data-roof3d-layer="'+name+'"]');if(input)input.checked=Boolean(visible);applyLayerVisibility();},debugState:function(){return{mode:siteRoofModel?.mode||'user_fallback',panelLayoutMode:panelLayoutMode,viewMode:viewMode,surfaces:reconstructedSurfaces.map(function(s){return{id:s.id,parentId:s.parentId,pitch:s.pitch,azimuth:s.azimuth,normal:s.normal,valid:s.valid,panelCount:s.panelCount,polygon:s.polygon};}),structures:reconstructedStructures.map(function(s){return{id:s.id,polygon:s.polygon,height:s.height};}),orientation:currentOrientationDeg,cameraAzimuth:cameraAzimuth,cameraElevation:cameraElevation,cameraViewSize:cameraDistance,cameraTarget:[cameraTargetX,cameraTargetY,cameraTargetZ],northDegrees:0,layers:{roofPlanes:roofPlanesGroup?.visible,panels:panelsGroup?.visible,structures:structuresGroup?.visible,boundary:boundaryGroup?.visible}};}};
  }
  if(typeof window!=='undefined')window.SIGNIX_ROOF3D_GEOMETRY={cameraFrameForBounds:cameraFrameForBounds,cameraFrameForRing:cameraFrameForRing,pointInRing:pointInRing,polygonFits:polygonFits,polygonFitReason:polygonFitReason,polygonsOverlap:polygonsOverlap,classificationGridValid:classificationGridValid,panelIntersectsClassification:panelIntersectsClassification,panelTouchesExcludedGrid:panelTouchesExcludedGrid,panelShadeVerified:panelShadeVerified,selectNonOverlappingPanels:selectNonOverlappingPanels,panelLayoutMetrics:panelLayoutMetrics,acceptedLayoutMetrics:acceptedLayoutMetrics,layoutAcceptanceState:layoutAcceptanceState,placementShortfallText:placementShortfallText,sumSurfacePanelCounts:sumSurfacePanelCounts,layoutCapacityKw:layoutCapacityKw,layoutDiffersFromEstimate:layoutDiffersFromEstimate,angularDifference:angularDifference,contractLocalRing:contractLocalRing,normalizedImagePolygonToLocal:normalizedImagePolygonToLocal,surfacePanelRect:surfacePanelRect,viewerCapability:viewerCapability,reconstructionPayloadState:reconstructionPayloadState,resolveViewerUnavailable:resolveViewerUnavailable,resolveReconstructionFailure:resolveReconstructionFailure,syncOrientationApplyButton:syncOrientationApplyButton};
  if(typeof document!=='undefined'){if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();}
})();
