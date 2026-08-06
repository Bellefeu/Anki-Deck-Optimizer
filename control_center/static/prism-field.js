"use strict";

/*
 * Prism Field
 * -----------
 * A real-time point-cloud renderer for the Control Center backdrop.
 *
 * Every form on screen is a ruled or swept surface sampled as a few hundred
 * thousand additive points. Density becomes luminance, so nothing is ever
 * drawn with a fill or a stroke; shapes emerge where sample paths crowd
 * together and dissolve where they thin out. Grain is the medium rather than
 * an overlay, which is what keeps the forms from reading as vector art.
 *
 * Pipeline: points -> HDR scene buffer -> bright pass -> two blur chains ->
 * composite with chromatic fringing, sensor grain, vignette and a soft
 * filmic curve.
 *
 * No dependencies, no network, no build step.
 */

(function () {
  const TAU = Math.PI * 2;

  const BODIES = {
    ruled: 0,     // doubly ruled hyperboloid, waist opening and closing
    weave: 1,     // straight lines strung between two Lissajous curves
    foam: 2,      // sphere veined by a Worley cell boundary field
    aperture: 3,  // volumetric shell orbiting a hard central void
    coil: 4,      // dense helical stack, a spool seen edge on
  };

  const VERT = `#version 300 es
precision highp float;

in vec2 aUV;
in float aSeed;

uniform float uTime;
uniform vec2  uRes;
uniform float uBodyPrev;
uniform float uBodyNext;
uniform float uMorph;      // 0 = wholly the previous body, 1 = wholly the next
uniform vec4  uFrame;      // xy = centre in NDC, z = scale, w = point size
uniform float uIntensity;
uniform float uWarm;

out vec3 vColor;
out float vAlpha;

const float TAU = 6.28318530718;

float h11(float p) {
  p = fract(p * 0.1031);
  p *= p + 33.33;
  p *= p + p;
  return fract(p);
}

vec3 h33(vec3 p) {
  p = fract(p * vec3(0.1031, 0.1030, 0.0973));
  p += dot(p, p.yxz + 33.33);
  return fract((p.xxy + p.yxx) * p.zyx);
}

/* First and second nearest feature distance of a jittered cell lattice.
   Cell walls sit where the two are equal, which is what draws the veins. */
vec2 worley2(vec3 p) {
  vec3 cell = floor(p);
  vec3 frac = fract(p);
  float f1 = 8.0;
  float f2 = 8.0;
  for (int x = -1; x <= 1; x++) {
    for (int y = -1; y <= 1; y++) {
      for (int z = -1; z <= 1; z++) {
        vec3 g = vec3(float(x), float(y), float(z));
        vec3 o = h33(cell + g);
        float d = length(g + o - frac);
        if (d < f1) { f2 = f1; f1 = d; }
        else if (d < f2) { f2 = d; }
      }
    }
  }
  return vec2(f1, f2);
}

float vnoise(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float n = 0.0;
  for (int c = 0; c < 8; c++) {
    vec3 g = vec3(float(c & 1), float((c >> 1) & 1), float((c >> 2) & 1));
    float w = mix(1.0 - f.x, f.x, g.x) * mix(1.0 - f.y, f.y, g.y) * mix(1.0 - f.z, f.z, g.z);
    n += h33(i + g).x * w;
  }
  return n;
}

float fbm(vec3 p) {
  return vnoise(p) * 0.55 + vnoise(p * 2.03) * 0.28 + vnoise(p * 4.11) * 0.17;
}

/* ---- bodies -------------------------------------------------------- */
/* Each returns a position and writes out an energy term, which the colour
   ramp reads. Energy is the local sense of "how much is happening here",
   not a lighting value. */

/* Collapse the first parameter onto a fixed number of strands. Without
   this the samples spread evenly and the form fills in as an opaque
   volume; with it, many samples land on the same path and the surface
   reads the way string art does, as a countable family of lines you can
   see through. */
float strand(float u, float count) {
  return (floor(u * count) + 0.5) / count;
}

vec3 bodyRuled(vec2 uv, float t, out float e) {
  float th = strand(uv.x, 132.0) * TAU;
  float s = uv.y;
  float twist = 1.05 + 1.15 * sin(t * 0.083);
  float bulge = 1.0 + 0.09 * sin(t * 0.121 + 1.7);
  vec3 a = vec3(cos(th), -1.12, sin(th)) * vec3(bulge, 1.0, bulge);
  float th2 = th + twist;
  vec3 b = vec3(cos(th2), 1.12, sin(th2)) * vec3(bulge, 1.0, bulge);
  vec3 p = mix(a, b, s);
  p.xz *= 1.0 + 0.05 * sin(t * 0.17 + p.y * 2.3);
  /* The waist is where the rulings crowd, so energy tracks the inverse
     radius rather than an arbitrary gradient. */
  float radius = length(p.xz);
  e = clamp(1.35 - radius, 0.0, 1.0);
  return p * 0.86;
}

vec3 bodyWeave(vec2 uv, float t, out float e) {
  float th = strand(uv.x, 300.0) * TAU;
  float s = uv.y;
  vec3 a = vec3(
    sin(2.0 * th + t * 0.061),
    sin(3.0 * th + 1.31 + t * 0.043),
    sin(1.0 * th + t * 0.052)
  );
  vec3 b = vec3(
    sin(3.0 * th + 2.14 + t * 0.071),
    sin(2.0 * th + 0.42 - t * 0.037),
    sin(4.0 * th + t * 0.049)
  );
  vec3 p = mix(a, b, s) * 1.28;
  /* Envelopes of a line family are brightest at the folds, which is where
     neighbouring rulings become parallel. */
  float fold = abs(sin(th * 2.5 + t * 0.05));
  e = 0.30 + 0.70 * (1.0 - fold) * (0.45 + 0.55 * (1.0 - abs(s * 2.0 - 1.0)));
  return p;
}

vec3 bodyFoam(vec2 uv, float t, out float e) {
  float z = 1.0 - 2.0 * uv.x;
  float r = sqrt(max(0.0, 1.0 - z * z));
  float a = uv.y * TAU;
  vec3 dir = vec3(r * cos(a), z, r * sin(a));
  /* Two lattices at different scales. A single one tiles into cells of
     almost identical size, which reads as a manufactured pattern; laying
     a coarse one over a fine one gives the uneven cell sizes that make
     soap film and cracked glaze look alive. */
  vec2 fine = worley2(dir * 4.60 + vec3(0.0, t * 0.021, t * 0.014));
  vec2 coarse = worley2(dir * 2.05 + vec3(t * 0.011, 4.7, 0.0));
  float veinFine = 1.0 - smoothstep(0.0, 0.115, fine.y - fine.x);
  float veinCoarse = 1.0 - smoothstep(0.0, 0.165, coarse.y - coarse.x);
  float vein = max(veinCoarse, veinFine * 0.62);
  vec3 p = dir * (1.13 + 0.045 * vein - 0.02 * coarse.x);
  e = vein * 0.94 + 0.06;
  return p;
}

vec3 bodyAperture(vec2 uv, float t, out float e) {
  float reach = pow(uv.x, 0.62);
  float radius = 0.60 + 1.05 * reach;
  float swirl = t * 0.10 - reach * 2.35;
  float th = uv.y * TAU + swirl;
  vec3 p = vec3(cos(th) * radius, sin(th) * radius, 0.0);
  vec3 drift = vec3(
    fbm(p * 1.15 + vec3(t * 0.030, 0.0, 0.0)),
    fbm(p * 1.15 + vec3(0.0, t * 0.026, 5.2)),
    fbm(p * 1.15 + vec3(9.4, 0.0, t * 0.022))
  ) - 0.5;
  p += drift * (0.28 + 0.92 * reach);
  /* Brightest right at the lip of the void, falling away outward. */
  e = pow(1.0 - reach, 1.6) * 0.85 + 0.15;
  return p * 0.92;
}

vec3 bodyCoil(vec2 uv, float t, out float e) {
  float along = strand(uv.x, 190.0);
  float th = uv.y * TAU + along * 46.0 + t * 0.045;
  float wobble = 0.017 * sin(along * 27.0 + t * 0.21);
  float radius = 0.82 + wobble;
  vec3 p = vec3(cos(th) * radius, along * 2.5 - 1.25, sin(th) * radius);
  e = 0.20 + 0.80 * pow(abs(sin(th)), 3.0);
  return p;
}

vec3 sampleBody(float which, vec2 uv, float t, out float e) {
  int id = int(which + 0.5);
  if (id == 1) return bodyWeave(uv, t, e);
  if (id == 2) return bodyFoam(uv, t, e);
  if (id == 3) return bodyAperture(uv, t, e);
  if (id == 4) return bodyCoil(uv, t, e);
  return bodyRuled(uv, t, e);
}

/* Yaw runs continuously; pitch only ever leans. Letting the pitch swing
   freely tips the forms until you are looking down their axis, which
   collapses a ruled surface into a pair of solid lobes and loses every
   line in it. */
mat3 spin(float t) {
  float yaw = t * 0.043;
  float pitch = 0.155 + 0.085 * sin(t * 0.0271);
  float cy = cos(yaw), sy = sin(yaw);
  float cp = cos(pitch), sp = sin(pitch);
  mat3 ry = mat3(cy, 0.0, -sy, 0.0, 1.0, 0.0, sy, 0.0, cy);
  mat3 rx = mat3(1.0, 0.0, 0.0, 0.0, cp, -sp, 0.0, sp, cp);
  return rx * ry;
}

void main() {
  float t = uTime;

  float eNext;
  vec3 pNext = sampleBody(uBodyNext, aUV, t, eNext);
  vec3 p = pNext;
  float energy = eNext;

  if (uMorph < 0.999) {
    float ePrev;
    vec3 pPrev = sampleBody(uBodyPrev, aUV, t, ePrev);
    p = mix(pPrev, pNext, uMorph);
    energy = mix(ePrev, eNext, uMorph);
  }

  /* Sub-sample scatter. Kept just under the strand spacing, so it reads as
     the grain of a long exposure rather than dissolving the lines it is
     scattered around. */
  vec3 jitter = h33(vec3(aSeed, aSeed * 1.7, aSeed * 3.1)) - 0.5;
  float breathe = 0.5 + 0.5 * sin(t * 0.23 + aSeed * 12.0);
  p += jitter * (0.005 + 0.009 * breathe);

  p = spin(t) * p;

  /* Weak perspective. Enough to give the forms a near and a far side
     without ever reading as a rendered 3D object. */
  float depth = 3.5 + p.z;
  float persp = 3.0 / max(depth, 0.35);
  vec2 flat2 = p.xy * persp * uFrame.z;

  float aspect = uRes.x / max(uRes.y, 1.0);
  vec2 ndc = vec2(flat2.x / aspect, flat2.y) + uFrame.xy;
  gl_Position = vec4(ndc, 0.0, 1.0);

  float near = clamp((p.z + 1.6) / 3.2, 0.0, 1.0);
  gl_PointSize = uFrame.w * (0.78 + 0.55 * near);

  /* Cold body, white where the samples pile up, and a rationed warm rim
     that only ever touches the brightest near-side crowding. */
  vec3 deep = vec3(0.035, 0.121, 0.470);
  vec3 mid = vec3(0.286, 0.514, 0.965);
  vec3 hot = vec3(0.898, 0.949, 1.000);
  float lift = clamp(energy * (0.42 + 0.58 * near), 0.0, 1.0);
  vec3 col = mix(deep, mid, smoothstep(0.0, 0.62, lift));
  col = mix(col, hot, smoothstep(0.58, 1.0, lift));
  col = mix(col, vec3(0.788, 0.545, 0.310), uWarm * smoothstep(0.70, 1.0, lift) * 0.55);

  float flicker = 0.72 + 0.28 * h11(aSeed * 91.7 + floor(t * 9.0) * 0.013);
  vColor = col;
  vAlpha = uIntensity * flicker * (0.30 + 0.70 * energy) * (0.55 + 0.45 * near);
}
`;

  const FRAG_POINT = `#version 300 es
precision highp float;

in vec3 vColor;
in float vAlpha;
out vec4 fragColor;

void main() {
  vec2 d = gl_PointCoord - 0.5;
  float r2 = dot(d, d) * 4.0;
  if (r2 > 1.0) discard;
  float falloff = exp(-r2 * 2.6);
  fragColor = vec4(vColor * vAlpha * falloff, 1.0);
}
`;

  const VERT_QUAD = `#version 300 es
precision highp float;
out vec2 vUV;
void main() {
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  vUV = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
`;

  const FRAG_BRIGHT = `#version 300 es
precision highp float;
in vec2 vUV;
uniform sampler2D uScene;
uniform float uThreshold;
out vec4 fragColor;
void main() {
  vec3 c = texture(uScene, vUV).rgb;
  float lum = dot(c, vec3(0.2126, 0.7152, 0.0722));
  float keep = max(lum - uThreshold, 0.0) / max(lum, 0.0001);
  fragColor = vec4(c * keep, 1.0);
}
`;

  const FRAG_BLUR = `#version 300 es
precision highp float;
in vec2 vUV;
uniform sampler2D uSource;
uniform vec2 uStep;
out vec4 fragColor;
void main() {
  /* Nine-tap gaussian folded into five bilinear fetches. */
  vec3 sum = texture(uSource, vUV).rgb * 0.2270270270;
  sum += texture(uSource, vUV + uStep * 1.3846153846).rgb * 0.3162162162;
  sum += texture(uSource, vUV - uStep * 1.3846153846).rgb * 0.3162162162;
  sum += texture(uSource, vUV + uStep * 3.2307692308).rgb * 0.0702702703;
  sum += texture(uSource, vUV - uStep * 3.2307692308).rgb * 0.0702702703;
  fragColor = vec4(sum, 1.0);
}
`;

  const FRAG_COMPOSITE = `#version 300 es
precision highp float;
in vec2 vUV;

uniform sampler2D uScene;
uniform sampler2D uBloomNear;
uniform sampler2D uBloomFar;
uniform vec2 uRes;
uniform float uTime;
uniform float uGrain;
uniform float uAberration;
uniform float uExposure;

out vec4 fragColor;

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

vec3 gather(vec2 uv) {
  return texture(uScene, uv).rgb
       + texture(uBloomNear, uv).rgb * 1.15
       + texture(uBloomFar, uv).rgb * 1.45;
}

void main() {
  vec2 off = vUV - 0.5;
  float shift = uAberration * dot(off, off);

  /* Lateral chromatic fringing, strongest toward the corners. The
     reference renders all show this at high-contrast silhouettes. */
  vec3 c;
  c.r = gather(vUV + off * shift).r;
  c.g = gather(vUV).g;
  c.b = gather(vUV - off * shift).b;

  c *= uExposure;

  /* Sensor floor. Held low enough to be felt rather than seen. */
  float g = hash21(vUV * uRes + fract(uTime) * 917.0);
  c += (g - 0.5) * uGrain;

  float vignette = 1.0 - 0.42 * dot(off, off);
  c *= clamp(vignette, 0.0, 1.0);

  c = max(c, 0.0);
  c = c / (c + 0.72);              // soft shoulder
  c = pow(c, vec3(0.9090909));     // gentle lift out of the blacks

  fragColor = vec4(c, 1.0);
}
`;

  function compile(gl, type, source, label) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(`Prism field: ${label} failed to compile. ${log}`);
    }
    return shader;
  }

  function link(gl, vertexSource, fragmentSource, label) {
    const program = gl.createProgram();
    const vs = compile(gl, gl.VERTEX_SHADER, vertexSource, `${label} vertex shader`);
    const fs = compile(gl, gl.FRAGMENT_SHADER, fragmentSource, `${label} fragment shader`);
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const log = gl.getProgramInfoLog(program);
      gl.deleteProgram(program);
      throw new Error(`Prism field: ${label} failed to link. ${log}`);
    }
    return program;
  }

  function uniformMap(gl, program) {
    const map = Object.create(null);
    const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < count; i += 1) {
      const info = gl.getActiveUniform(program, i);
      if (info) map[info.name] = gl.getUniformLocation(program, info.name);
    }
    return map;
  }

  /* Stratified samples in shuffled order, so drawing any prefix of the
     buffer is still an unbiased sample of the whole surface. That is what
     lets quality scale down without the form falling apart. */
  function buildSamples(count) {
    const columns = Math.max(2, Math.round(Math.sqrt(count * 1.6)));
    const rows = Math.max(2, Math.ceil(count / columns));
    const total = columns * rows;
    const uv = new Float32Array(total * 2);
    const seed = new Float32Array(total);
    const order = new Uint32Array(total);

    let write = 0;
    for (let row = 0; row < rows; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        uv[write * 2] = (column + Math.random()) / columns;
        uv[write * 2 + 1] = (row + Math.random()) / rows;
        seed[write] = Math.random() * 977.0;
        order[write] = write;
        write += 1;
      }
    }
    for (let i = total - 1; i > 0; i -= 1) {
      const j = (Math.random() * (i + 1)) | 0;
      const tmp = order[i];
      order[i] = order[j];
      order[j] = tmp;
    }

    const uvOut = new Float32Array(total * 2);
    const seedOut = new Float32Array(total);
    for (let i = 0; i < total; i += 1) {
      const source = order[i];
      uvOut[i * 2] = uv[source * 2];
      uvOut[i * 2 + 1] = uv[source * 2 + 1];
      seedOut[i] = seed[source];
    }
    return { uv: uvOut, seed: seedOut, total };
  }

  class Field {
    constructor(canvas, options) {
      this.canvas = canvas;
      this.options = options || {};
      this.gl = null;
      this.running = false;
      this.disposed = false;
      this.frameHandle = 0;

      this.clock = 0;
      this.lastStamp = 0;
      this.frameCost = 16.7;
      this.sampleBudget = 1;

      this.bodyPrev = BODIES.ruled;
      this.bodyNext = BODIES.ruled;
      this.morph = 1;

      this.frame = { x: 0.42, y: 0.0, scale: 1.0, point: 1.15 };
      this.target = { x: 0.42, y: 0.0, scale: 1.0, point: 1.15 };
      this.intensity = 0;
      this.targetIntensity = 1;
      this.warm = 0;
      this.targetWarm = 0;
      this.composed = false;
      this.dirty = true;

      this.reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      this.onLost = (event) => {
        event.preventDefault();
        this.stop();
        this.gl = null;
      };
      this.onRestored = () => {
        if (this.disposed) return;
        if (this.init()) this.start();
      };
      canvas.addEventListener("webglcontextlost", this.onLost, false);
      canvas.addEventListener("webglcontextrestored", this.onRestored, false);
    }

    init() {
      const gl = this.canvas.getContext("webgl2", {
        alpha: false,
        antialias: false,
        depth: false,
        stencil: false,
        premultipliedAlpha: false,
        powerPreference: "high-performance",
        preserveDrawingBuffer: false,
      });
      if (!gl) return false;
      this.gl = gl;

      /* Additive point accumulation blows well past 1.0 in the dense
         regions, and that overshoot is exactly what the bright pass needs
         to find. Without a float target the highlights clip flat instead. */
      this.hdr = Boolean(gl.getExtension("EXT_color_buffer_float"))
        || Boolean(gl.getExtension("EXT_color_buffer_half_float"));
      gl.getExtension("OES_texture_float_linear");

      this.programs = {
        points: link(gl, VERT, FRAG_POINT, "point"),
        bright: link(gl, VERT_QUAD, FRAG_BRIGHT, "bright pass"),
        blur: link(gl, VERT_QUAD, FRAG_BLUR, "blur"),
        composite: link(gl, VERT_QUAD, FRAG_COMPOSITE, "composite"),
      };
      this.uniforms = {
        points: uniformMap(gl, this.programs.points),
        bright: uniformMap(gl, this.programs.bright),
        blur: uniformMap(gl, this.programs.blur),
        composite: uniformMap(gl, this.programs.composite),
      };

      const requested = this.options.samples || 220000;
      const samples = buildSamples(requested);
      this.sampleCount = samples.total;

      this.pointsVAO = gl.createVertexArray();
      gl.bindVertexArray(this.pointsVAO);

      this.uvBuffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.uvBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, samples.uv, gl.STATIC_DRAW);
      const uvLocation = gl.getAttribLocation(this.programs.points, "aUV");
      gl.enableVertexAttribArray(uvLocation);
      gl.vertexAttribPointer(uvLocation, 2, gl.FLOAT, false, 0, 0);

      this.seedBuffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.seedBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, samples.seed, gl.STATIC_DRAW);
      const seedLocation = gl.getAttribLocation(this.programs.points, "aSeed");
      gl.enableVertexAttribArray(seedLocation);
      gl.vertexAttribPointer(seedLocation, 1, gl.FLOAT, false, 0, 0);

      gl.bindVertexArray(null);

      this.quadVAO = gl.createVertexArray();

      /* resize() is a no-op when the dimensions match what it last built,
         and after a context loss they do. Clearing them forces the render
         targets to actually be recreated on the restored context. */
      this.targets = {};
      this.width = 0;
      this.height = 0;
      this.resize();
      return true;
    }

    colorFormat() {
      const gl = this.gl;
      return this.hdr ? gl.RGBA16F : gl.RGBA8;
    }

    makeTarget(width, height) {
      const gl = this.gl;
      const texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texStorage2D(gl.TEXTURE_2D, 1, this.colorFormat(), width, height);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      const framebuffer = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
      const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.bindTexture(gl.TEXTURE_2D, null);
      if (status !== gl.FRAMEBUFFER_COMPLETE) {
        gl.deleteTexture(texture);
        gl.deleteFramebuffer(framebuffer);
        /* A driver that advertises float targets but will not render to
           them drops us to 8-bit once, then we retry rather than spend the
           session drawing into an incomplete buffer. */
        if (this.hdr) {
          this.hdr = false;
          return this.makeTarget(width, height);
        }
        throw new Error("Prism field: no renderable colour buffer format.");
      }
      return { texture, framebuffer, width, height };
    }

    dropTarget(target) {
      if (!target) return;
      this.gl.deleteTexture(target.texture);
      this.gl.deleteFramebuffer(target.framebuffer);
    }

    resize() {
      const gl = this.gl;
      if (!gl) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const cssWidth = this.canvas.clientWidth || window.innerWidth;
      const cssHeight = this.canvas.clientHeight || window.innerHeight;
      const width = Math.max(2, Math.round(cssWidth * ratio));
      const height = Math.max(2, Math.round(cssHeight * ratio));
      if (width === this.width && height === this.height) return;

      this.width = width;
      this.height = height;
      this.canvas.width = width;
      this.canvas.height = height;

      for (const key of Object.keys(this.targets)) this.dropTarget(this.targets[key]);

      const halfW = Math.max(1, width >> 1);
      const halfH = Math.max(1, height >> 1);
      const wideW = Math.max(1, width >> 3);
      const wideH = Math.max(1, height >> 3);

      this.targets = {
        scene: this.makeTarget(width, height),
        nearA: this.makeTarget(halfW, halfH),
        nearB: this.makeTarget(halfW, halfH),
        farA: this.makeTarget(wideW, wideH),
        farB: this.makeTarget(wideW, wideH),
      };
      this.dirty = true;
    }

    /* Views hand the field a composition rather than a shape name, so the
       backdrop is framed against the page it sits behind. */
    setScene(name, options) {
      const next = BODIES[name] === undefined ? BODIES.ruled : BODIES[name];
      if (next !== this.bodyNext) {
        /* The outgoing shape is whatever was being formed. There is no
           interpolated state to hand over, so switching views twice inside
           one transition steps rather than glides. At a 1.5s morph that
           needs a deliberate double-click to provoke. */
        this.bodyPrev = this.bodyNext;
        this.bodyNext = next;
        this.morph = 0;
      }
      this.dirty = true;
      const settings = options || {};
      this.target.x = settings.x === undefined ? 0.42 : settings.x;
      this.target.y = settings.y === undefined ? 0.0 : settings.y;
      this.target.scale = settings.scale === undefined ? 1.0 : settings.scale;
      this.target.point = settings.point === undefined ? 1.15 : settings.point;
      this.targetIntensity = settings.intensity === undefined ? 1.0 : settings.intensity;
      this.targetWarm = settings.warm === undefined ? 0.0 : settings.warm;

      /* The first composition arrives already framed. Easing into it from
         a default would mean the opening seconds show a shape that is the
         wrong size for the page, and on a backgrounded tab, where frames
         are rationed, it would stay wrong for a long time. Brightness
         still rises from nothing, because that reads as the field coming
         up rather than as a layout correcting itself. */
      if (!this.composed) {
        this.composed = true;
        this.frame.x = this.target.x;
        this.frame.y = this.target.y;
        this.frame.scale = this.target.scale;
        this.frame.point = this.target.point;
        this.warm = this.targetWarm;
      }
    }

    start() {
      if (this.running || !this.gl) return;
      this.running = true;
      this.lastStamp = 0;
      this.frameHandle = requestAnimationFrame(this.tick);
    }

    stop() {
      this.running = false;
      if (this.frameHandle) cancelAnimationFrame(this.frameHandle);
      this.frameHandle = 0;
    }

    tick = (stamp) => {
      if (!this.running || !this.gl) return;
      const delta = this.lastStamp ? Math.min(stamp - this.lastStamp, 66) : 16.7;
      this.lastStamp = stamp;
      this.frameCost += (delta - this.frameCost) * 0.06;

      /* Quality follows the machine. Sample budget only ever moves a
         little per frame, so the density change is never a visible pop. */
      if (this.frameCost > 24 && this.sampleBudget > 0.34) {
        this.sampleBudget = Math.max(0.34, this.sampleBudget - 0.012);
      } else if (this.frameCost < 15 && this.sampleBudget < 1) {
        this.sampleBudget = Math.min(1, this.sampleBudget + 0.004);
      }

      if (!this.reducedMotion) this.clock += delta / 1000;

      const ease = this.reducedMotion ? 1 : 1 - Math.pow(0.001, delta / 1000);
      this.frame.x += (this.target.x - this.frame.x) * ease;
      this.frame.y += (this.target.y - this.frame.y) * ease;
      this.frame.scale += (this.target.scale - this.frame.scale) * ease;
      this.frame.point += (this.target.point - this.frame.point) * ease;
      this.intensity += (this.targetIntensity - this.intensity) * ease;
      this.warm += (this.targetWarm - this.warm) * ease;
      if (this.morph < 1) this.morph = Math.min(1, this.morph + delta / 1500);

      this.resize();
      /* With motion reduced the clock is frozen and every ease resolves in
         one step, so consecutive frames are identical. Redrawing them
         would spin the GPU to produce the same still image. */
      if (!this.reducedMotion || this.dirty) {
        this.dirty = false;
        this.render();
      }
      this.frameHandle = requestAnimationFrame(this.tick);
    };

    drawQuad() {
      const gl = this.gl;
      gl.bindVertexArray(this.quadVAO);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.bindVertexArray(null);
    }

    bindTarget(target) {
      const gl = this.gl;
      gl.bindFramebuffer(gl.FRAMEBUFFER, target.framebuffer);
      gl.viewport(0, 0, target.width, target.height);
    }

    blur(source, destination, dx, dy) {
      const gl = this.gl;
      const program = this.programs.blur;
      const u = this.uniforms.blur;
      gl.useProgram(program);
      this.bindTarget(destination);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, source.texture);
      gl.uniform1i(u.uSource, 0);
      gl.uniform2f(u.uStep, dx / source.width, dy / source.height);
      this.drawQuad();
    }

    render() {
      const gl = this.gl;
      const t = this.targets;

      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);

      /* Scene: additive accumulation on black. */
      this.bindTarget(t.scene);
      gl.clearColor(0, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE);

      const program = this.programs.points;
      const u = this.uniforms.points;
      gl.useProgram(program);
      gl.uniform1f(u.uTime, this.clock);
      gl.uniform2f(u.uRes, this.width, this.height);
      gl.uniform1f(u.uBodyPrev, this.bodyPrev);
      gl.uniform1f(u.uBodyNext, this.bodyNext);
      gl.uniform1f(u.uMorph, this.easeMorph());
      gl.uniform4f(u.uFrame, this.frame.x, this.frame.y, this.frame.scale,
        this.frame.point * Math.min(window.devicePixelRatio || 1, 2));
      /* Total light in the frame is what should stay fixed, not light per
         sample. Dividing by the budget means a machine that drops to a
         third of the samples gets a grainier field, not a darker one. */
      gl.uniform1f(u.uIntensity, this.intensity * 1.2 / this.sampleBudget);
      gl.uniform1f(u.uWarm, this.warm);

      gl.bindVertexArray(this.pointsVAO);
      const drawCount = Math.max(1000, Math.floor(this.sampleCount * this.sampleBudget));
      gl.drawArrays(gl.POINTS, 0, drawCount);
      gl.bindVertexArray(null);

      gl.disable(gl.BLEND);

      /* Bright pass into the near bloom chain. */
      gl.useProgram(this.programs.bright);
      this.bindTarget(t.nearA);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, t.scene.texture);
      gl.uniform1i(this.uniforms.bright.uScene, 0);
      gl.uniform1f(this.uniforms.bright.uThreshold, this.hdr ? 0.16 : 0.09);
      this.drawQuad();

      this.blur(t.nearA, t.nearB, 1, 0);
      this.blur(t.nearB, t.nearA, 0, 1);

      /* Second, much wider chain for the long halo the renders carry. The
         first step doubles as the prefilter for a four-to-one downsample,
         so it takes a wider stride than the rest. */
      this.blur(t.nearA, t.farA, 2, 0);
      this.blur(t.farA, t.farB, 0, 1);
      this.blur(t.farB, t.farA, 1, 0);
      this.blur(t.farA, t.farB, 0, 1);

      /* Composite to the screen. */
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, this.width, this.height);
      const c = this.programs.composite;
      const cu = this.uniforms.composite;
      gl.useProgram(c);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, t.scene.texture);
      gl.uniform1i(cu.uScene, 0);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, t.nearA.texture);
      gl.uniform1i(cu.uBloomNear, 1);
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, t.farB.texture);
      gl.uniform1i(cu.uBloomFar, 2);
      gl.uniform2f(cu.uRes, this.width, this.height);
      gl.uniform1f(cu.uTime, this.clock);
      gl.uniform1f(cu.uGrain, 0.030);
      gl.uniform1f(cu.uAberration, 0.030);
      gl.uniform1f(cu.uExposure, 1.30);
      this.drawQuad();
    }

    easeMorph() {
      const x = this.morph;
      return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
    }

    dispose() {
      this.disposed = true;
      this.stop();
      this.canvas.removeEventListener("webglcontextlost", this.onLost);
      this.canvas.removeEventListener("webglcontextrestored", this.onRestored);
      const gl = this.gl;
      if (!gl) return;
      for (const key of Object.keys(this.targets || {})) this.dropTarget(this.targets[key]);
      this.targets = {};
      for (const key of Object.keys(this.programs || {})) gl.deleteProgram(this.programs[key]);
      gl.deleteBuffer(this.uvBuffer);
      gl.deleteBuffer(this.seedBuffer);
      gl.deleteVertexArray(this.pointsVAO);
      gl.deleteVertexArray(this.quadVAO);
      this.gl = null;
    }
  }

  function mount(canvas, options) {
    let field = null;
    try {
      field = new Field(canvas, options);
      if (!field.init()) throw new Error("WebGL 2 is unavailable.");
    } catch (error) {
      if (field) field.dispose();
      /* A machine without WebGL 2 still gets the palette, just held still. */
      document.documentElement.classList.add("field-unavailable");
      if (window.console && console.info) console.info(String(error.message || error));
      return null;
    }

    field.start();

    window.addEventListener("resize", () => field.resize(), { passive: true });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) field.stop();
      else field.start();
    });
    return field;
  }

  window.PrismField = { mount, BODIES };
})();
