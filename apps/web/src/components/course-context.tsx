"use client";

import { createContext, useContext, useEffect, useMemo } from "react";
import type { CourseCreateRequest, CourseSummary } from "@course-kg/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createCourse, fetchCourses } from "@/lib/api";
import { useLocalStorage } from "@/hooks/use-local-storage";

type CourseContextValue = {
  courses: CourseSummary[];
  selectedCourseId: string | null;
  selectedCourse: CourseSummary | null;
  isLoading: boolean;
  error: Error | null;
  setSelectedCourseId: (value: string | null) => void;
  createCourseSpace: (payload: CourseCreateRequest) => Promise<CourseSummary>;
  isCreating: boolean;
};

const CourseContext = createContext<CourseContextValue | null>(null);

export function CourseProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [selectedCourseId, setSelectedCourseId] = useLocalStorage<string | null>("course.selectedId", null);
  const coursesQuery = useQuery({ queryKey: ["courses"], queryFn: fetchCourses });

  useEffect(() => {
    if (!coursesQuery.data) {
      return;
    }
    const courses = coursesQuery.data;
    if (courses.length === 0) {
      if (selectedCourseId !== null) {
        setSelectedCourseId(null);
      }
      return;
    }
    if (!selectedCourseId || !courses.some((course) => course.id === selectedCourseId)) {
      setSelectedCourseId(courses[0].id);
    }
  }, [coursesQuery.data, selectedCourseId, setSelectedCourseId]);

  const createCourseMutation = useMutation({
    mutationFn: (payload: CourseCreateRequest) => createCourse(payload),
    onSuccess: async (course) => {
      queryClient.setQueryData<CourseSummary[]>(["courses"], (current) => {
        const base = current ?? [];
        return base.some((item) => item.id === course.id) ? base : [...base, course];
      });
      setSelectedCourseId(course.id);
      await queryClient.invalidateQueries({ queryKey: ["courses"] });
    },
  });

  const courses = useMemo(() => coursesQuery.data ?? [], [coursesQuery.data]);
  const selectedCourse = courses.find((course) => course.id === selectedCourseId) ?? null;

  const value = useMemo<CourseContextValue>(
    () => ({
      courses,
      selectedCourseId,
      selectedCourse,
      isLoading: coursesQuery.isLoading,
      error: (coursesQuery.error as Error | null) ?? null,
      setSelectedCourseId,
      createCourseSpace: createCourseMutation.mutateAsync,
      isCreating: createCourseMutation.isPending,
    }),
    [
      courses,
      selectedCourseId,
      selectedCourse,
      coursesQuery.isLoading,
      coursesQuery.error,
      setSelectedCourseId,
      createCourseMutation.mutateAsync,
      createCourseMutation.isPending,
    ],
  );

  return <CourseContext.Provider value={value}>{children}</CourseContext.Provider>;
}

export function useCourseContext() {
  const context = useContext(CourseContext);
  if (!context) {
    throw new Error("useCourseContext must be used within CourseProvider");
  }
  return context;
}
