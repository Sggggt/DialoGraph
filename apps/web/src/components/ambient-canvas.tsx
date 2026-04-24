"use client";

import { useEffect, useRef } from "react";

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
};

export function AmbientCanvas() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }

    const particles: Particle[] = [];
    let animationFrame = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.min(64, Math.floor(window.innerWidth / 36));
      particles.length = 0;
      for (let index = 0; index < count; index += 1) {
        particles.push({
          x: Math.random() * window.innerWidth,
          y: Math.random() * window.innerHeight,
          vx: (Math.random() - 0.5) * 0.12,
          vy: (Math.random() - 0.5) * 0.12,
          radius: Math.random() * 1.1 + 0.4,
        });
      }
    };

    const draw = () => {
      context.clearRect(0, 0, window.innerWidth, window.innerHeight);

      for (const particle of particles) {
        particle.x += particle.vx;
        particle.y += particle.vy;
        if (particle.x < 0 || particle.x > window.innerWidth) particle.vx *= -1;
        if (particle.y < 0 || particle.y > window.innerHeight) particle.vy *= -1;

        context.beginPath();
        context.fillStyle = "rgba(104, 210, 255, 0.28)";
        context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
        context.fill();
      }

      for (let index = 0; index < particles.length; index += 1) {
        const source = particles[index];
        for (let inner = index + 1; inner < particles.length; inner += 1) {
          const target = particles[inner];
          const dx = source.x - target.x;
          const dy = source.y - target.y;
          const distance = Math.sqrt(dx * dx + dy * dy);
          if (distance > 90) continue;
          context.beginPath();
          context.strokeStyle = `rgba(86, 196, 255, ${0.045 - distance / 2600})`;
          context.lineWidth = 1;
          context.moveTo(source.x, source.y);
          context.lineTo(target.x, target.y);
          context.stroke();
        }
      }

      animationFrame = window.requestAnimationFrame(draw);
    };

    resize();
    draw();
    window.addEventListener("resize", resize);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvasRef} aria-hidden className="pointer-events-none fixed inset-0 -z-10 opacity-50" />;
}
