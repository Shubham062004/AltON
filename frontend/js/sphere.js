// sphere.js - Three.js AI Sphere Visualization

const container = document.getElementById('canvas-container');

// Scene Setup
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000);
camera.position.z = 5;

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
container.appendChild(renderer.domElement);

// Sphere Geometry & Material
const geometry = new THREE.IcosahedronGeometry(1.2, 64);

// Custom Shader Material for futuristic wave distortion
const vertexShader = `
    uniform float time;
    uniform float intensity;
    uniform float speed;
    
    varying vec2 vUv;
    varying vec3 vNormal;
    varying float vDisplacement;
    
    // Simplex noise function
    vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
    vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
    vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
    vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
    
    float snoise(vec3 v) {
        const vec2  C = vec2(1.0/6.0, 1.0/3.0) ;
        const vec4  D = vec4(0.0, 0.5, 1.0, 2.0);
        vec3 i  = floor(v + dot(v, C.yyy) );
        vec3 x0 = v - i + dot(i, C.xxx) ;
        vec3 g = step(x0.yzx, x0.xyz);
        vec3 l = 1.0 - g;
        vec3 i1 = min( g.xyz, l.zxy );
        vec3 i2 = max( g.xyz, l.zxy );
        vec3 x1 = x0 - i1 + C.xxx;
        vec3 x2 = x0 - i2 + C.yyy;
        vec3 x3 = x0 - D.yyy;
        i = mod289(i);
        vec4 p = permute( permute( permute(
                    i.z + vec4(0.0, i1.z, i2.z, 1.0 ))
                  + i.y + vec4(0.0, i1.y, i2.y, 1.0 ))
                  + i.x + vec4(0.0, i1.x, i2.x, 1.0 ));
        float n_ = 0.142857142857;
        vec3  ns = n_ * D.wyz - D.xzx;
        vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
        vec4 x_ = floor(j * ns.z);
        vec4 y_ = floor(j - 7.0 * x_ );
        vec4 x = x_ *ns.x + ns.yyyy;
        vec4 y = y_ *ns.x + ns.yyyy;
        vec4 h = 1.0 - abs(x) - abs(y);
        vec4 b0 = vec4( x.xy, y.xy );
        vec4 b1 = vec4( x.zw, y.zw );
        vec4 s0 = floor(b0)*2.0 + 1.0;
        vec4 s1 = floor(b1)*2.0 + 1.0;
        vec4 sh = -step(h, vec4(0.0));
        vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy ;
        vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww ;
        vec3 p0 = vec3(a0.xy,h.x);
        vec3 p1 = vec3(a0.zw,h.y);
        vec3 p2 = vec3(a1.xy,h.z);
        vec3 p3 = vec3(a1.zw,h.w);
        vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
        p0 *= norm.x;
        p1 *= norm.y;
        p2 *= norm.z;
        p3 *= norm.w;
        vec4 m = max(0.5 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
        m = m * m;
        return 42.0 * dot( m*m, vec4( dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3) ) );
    }

    void main() {
        vUv = uv;
        vNormal = normal;
        
        float noise = snoise(vec3(position.x * 2.0, position.y * 2.0, position.z * 2.0 + time * speed));
        vec3 newPosition = position + normal * noise * intensity;
        vDisplacement = noise;
        
        gl_Position = projectionMatrix * modelViewMatrix * vec4(newPosition, 1.0);
    }
`;

const fragmentShader = `
    uniform vec3 color1;
    uniform vec3 color2;
    
    varying vec2 vUv;
    varying vec3 vNormal;
    varying float vDisplacement;
    
    void main() {
        float mixValue = (vDisplacement + 1.0) * 0.5;
        vec3 finalColor = mix(color1, color2, mixValue);
        
        // Add rim lighting
        float intensity = pow(0.6 - dot(vNormal, vec3(0, 0, 1.0)), 2.0);
        finalColor += vec3(intensity) * color2;
        
        gl_FragColor = vec4(finalColor, 0.85);
    }
`;

// Define States
const STATES = {
    IDLE: {
        intensity: 0.15,
        speed: 0.5,
        color1: new THREE.Color(0x000b18),
        color2: new THREE.Color(0x00f0ff) // Neon Blue
    },
    LISTENING: {
        intensity: 0.3,
        speed: 1.5,
        color1: new THREE.Color(0x001a00),
        color2: new THREE.Color(0x00ff88) // Neon Green
    },
    THINKING: {
        intensity: 0.5,
        speed: 3.0,
        color1: new THREE.Color(0x1a001a),
        color2: new THREE.Color(0xff0055) // Accent Red/Purple
    },
    SPEAKING: {
        intensity: 0.4,
        speed: 2.0,
        color1: new THREE.Color(0x1a001a),
        color2: new THREE.Color(0xaa00ff) // Deep Purple
    }
};

let currentState = STATES.IDLE;
let targetState = STATES.IDLE;

const material = new THREE.ShaderMaterial({
    uniforms: {
        time: { value: 0 },
        intensity: { value: currentState.intensity },
        speed: { value: currentState.speed },
        color1: { value: currentState.color1 },
        color2: { value: currentState.color2 }
    },
    vertexShader: vertexShader,
    fragmentShader: fragmentShader,
    wireframe: true,
    transparent: true
});

const sphere = new THREE.Mesh(geometry, material);
scene.add(sphere);

// Handle State Changes
window.addEventListener('ai-state-change', (e) => {
    const stateName = e.detail;
    if (STATES[stateName]) {
        targetState = STATES[stateName];
    }
});

// Animation Loop
const clock = new THREE.Clock();

function animate() {
    requestAnimationFrame(animate);
    const elapsedTime = clock.getElapsedTime();
    
    // Smooth transition between states
    material.uniforms.intensity.value += (targetState.intensity - material.uniforms.intensity.value) * 0.05;
    material.uniforms.speed.value += (targetState.speed - material.uniforms.speed.value) * 0.05;
    
    material.uniforms.color1.value.lerp(targetState.color1, 0.05);
    material.uniforms.color2.value.lerp(targetState.color2, 0.05);
    
    material.uniforms.time.value = elapsedTime;
    
    // Slight rotation
    sphere.rotation.y += 0.002;
    sphere.rotation.x += 0.001;
    
    renderer.render(scene, camera);
}

// Window Resize
window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

animate();
